# SPDX-License-Identifier: Apache-2.0
"""
hsb_worker.py — HSB (Holoscan Sensor Bridge) VB1940 フレーム配信ワーカー

hololink/holoscan が動く Python 3.12 venv 側で実行され、
受信フレーム (RGB8) を /dev/shm 上の seqlock 付き共有バッファへ書き続ける。
LeRobot プラグイン (任意の Python) はこのバッファを mmap で読む。

このファイルは LeRobot 側の環境からは import されない (subprocess 起動専用)。

共有メモリレイアウト (little-endian):
    offset 0:  magic   u32  = 0x48534243 ("HSBC")
    offset 4:  version u32  = 1
    offset 8:  width   u32
    offset 12: height  u32
    offset 16: channels u32 (3 = RGB)
    offset 20: fps     u32
    offset 24: seq     u64  (奇数=書き込み中, 偶数=安定)
    offset 32: ts      f64  (frame timestamp, time.time())
    offset 64: frame bytes (width*height*channels, RGB8)
"""

import argparse
import ctypes
import logging
import mmap
import os
import signal
import struct
import subprocess
import sys
import threading
import time

# holoscan deb の python バインディングを venv から見えるように
_HOLOSCAN_PY = "/opt/nvidia/holoscan/python/lib"
if _HOLOSCAN_PY not in sys.path and os.path.isdir(_HOLOSCAN_PY):
    sys.path.insert(0, _HOLOSCAN_PY)

import cuda.bindings.driver as cuda  # noqa: E402
import holoscan  # noqa: E402

import hololink as hololink_module  # noqa: E402

HEADER_FMT = "<IIIIIIQd"  # magic, version, w, h, ch, fps, seq, ts
HEADER_SIZE = 64
MAGIC = 0x48534243

MODE_TABLE = {  # mode -> (width, height, fps)
    0: (2560, 1984, 30),
    1: (1920, 1080, 30),
    2: (2560, 1984, 60),
    3: (2560, 1984, 30),
}

_stop_event = threading.Event()


class ShmWriterOp(holoscan.core.Operator):
    """demosaic/gamma 後の RGBA16 テンソルを RGB8 にして共有メモリへ書く"""

    def __init__(self, fragment, *args, shm_path, width, height, fps,
                 condition=None, **kwargs):
        self._shm_path = shm_path
        self._width = width
        self._height = height
        self._fps = fps
        self._condition = condition
        self._seq = 0
        self._announced = False
        frame_bytes = width * height * 3
        # ファイルバック mmap (/dev/shm): resource_tracker 問題を回避できる
        with open(shm_path, "wb") as f:
            f.truncate(HEADER_SIZE + frame_bytes)
        self._f = open(shm_path, "r+b")
        self._mm = mmap.mmap(self._f.fileno(), HEADER_SIZE + frame_bytes)
        self._write_header(seq=0, ts=0.0)
        super().__init__(fragment, *args, **kwargs)

    def _write_header(self, seq, ts):
        struct.pack_into(HEADER_FMT, self._mm, 0, MAGIC, 1, self._width,
                         self._height, 3, self._fps, seq, ts)

    def setup(self, spec):
        spec.input("input")

    def compute(self, op_input, op_output, context):
        import cupy as cp

        message = op_input.receive("input")
        tensor = None
        if hasattr(message, "get"):
            tensor = message.get("")
        if tensor is None:
            try:
                tensor = next(iter(dict(message).values()))
            except Exception:
                return

        if _stop_event.is_set():
            if self._condition is not None:
                self._condition.disable_tick()
            return

        arr = cp.asarray(tensor)  # (H, W, 4) uint16 RGBA
        rgb8 = (arr[..., :3] >> 8).astype(cp.uint8).get()  # host RGB8

        # seqlock write: 奇数 = 書き込み中
        self._seq += 1
        self._write_header(self._seq, 0.0)
        self._mm[HEADER_SIZE:HEADER_SIZE + rgb8.nbytes] = rgb8.tobytes()
        self._seq += 1
        self._write_header(self._seq, time.time())

        if not self._announced:
            self._announced = True
            print(f"READY {self._width} {self._height} {self._fps}", flush=True)


class WorkerApp(holoscan.core.Application):
    def __init__(self, cuda_context, cuda_device_ordinal, hololink_channel,
                 camera, camera_mode, shm_path, fps):
        super().__init__()
        self._cuda_context = cuda_context
        self._cuda_device_ordinal = cuda_device_ordinal
        self._hololink_channel = hololink_channel
        self._camera = camera
        self._camera_mode = camera_mode
        self._shm_path = shm_path
        self._fps = fps

    def compose(self):
        self._ok = holoscan.conditions.BooleanCondition(
            self, name="ok", enable_tick=True)
        self._camera.set_mode(self._camera_mode)

        csi_to_bayer_pool = holoscan.resources.BlockMemoryPool(
            self, name="pool", storage_type=1,
            block_size=self._camera._width * ctypes.sizeof(ctypes.c_uint16)
            * self._camera._height,
            num_blocks=2)
        csi_to_bayer = hololink_module.operators.CsiToBayerOp(
            self, name="csi_to_bayer", allocator=csi_to_bayer_pool,
            cuda_device_ordinal=self._cuda_device_ordinal)
        self._camera.configure_converter(csi_to_bayer)

        receiver = hololink_module.operators.LinuxReceiverOperator(
            self, self._ok, name="receiver",
            frame_size=csi_to_bayer.get_csi_length(),
            frame_context=self._cuda_context,
            hololink_channel=self._hololink_channel,
            device=self._camera)

        pixel_format = self._camera.pixel_format()
        bayer_format = self._camera.bayer_format()
        image_processor = hololink_module.operators.ImageProcessorOp(
            self, name="image_processor", optical_black=8,
            bayer_format=bayer_format.value, pixel_format=pixel_format.value,
            r_gain=1.5, g_gain=1.0, b_gain=2.3, is_manual=True)

        bayer_pool = holoscan.resources.BlockMemoryPool(
            self, name="pool", storage_type=1,
            block_size=self._camera._width * 4
            * ctypes.sizeof(ctypes.c_uint16) * self._camera._height,
            num_blocks=2)
        demosaic = holoscan.operators.BayerDemosaicOp(
            self, name="demosaic", pool=bayer_pool, generate_alpha=True,
            alpha_value=65535, bayer_grid_pos=bayer_format.value,
            interpolation_mode=0)

        gamma = hololink_module.operators.GammaCorrectionOp(
            self, name="gamma_correction",
            cuda_device_ordinal=self._cuda_device_ordinal, gamma=1.2)

        writer = ShmWriterOp(
            self, name="shm_writer", shm_path=self._shm_path,
            width=self._camera._width, height=self._camera._height,
            fps=self._fps, condition=self._ok)

        self.add_flow(receiver, csi_to_bayer, {("output", "input")})
        self.add_flow(csi_to_bayer, image_processor, {("output", "input")})
        self.add_flow(image_processor, demosaic, {("output", "receiver")})
        self.add_flow(demosaic, gamma, {("transmitter", "input")})
        self.add_flow(gamma, writer, {("output", "input")})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hololink", default="192.168.0.2")
    parser.add_argument("--camera-mode", type=int, default=0)
    parser.add_argument("--shm-path", required=True)
    parser.add_argument("--mac-id", default="ff:ff:ff:ff:ff:ff")
    parser.add_argument("--reset", action="store_true",
                        help="hololink.reset() を実行する (リンク不安定時は非推奨)")
    parser.add_argument("--skip-setup-clock", action="store_true")
    parser.add_argument("--log-level", type=int, default=20)
    args = parser.parse_args()

    hololink_module.logging_level(args.log_level)
    signal.signal(signal.SIGTERM, lambda *_: _stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: _stop_event.set())

    _, _, fps = MODE_TABLE.get(args.camera_mode, (0, 0, 30))

    (cu_result,) = cuda.cuInit(0)
    assert cu_result == cuda.CUresult.CUDA_SUCCESS
    cu_result, cu_device = cuda.cuDeviceGet(0)
    assert cu_result == cuda.CUresult.CUDA_SUCCESS
    cu_result, cu_context = cuda.cuDevicePrimaryCtxRetain(cu_device)
    assert cu_result == cuda.CUresult.CUDA_SUCCESS

    proc = subprocess.Popen(["hololink", "set-ip", args.mac_id, args.hololink])
    try:
        channel_metadata = hololink_module.Enumerator.find_channel(
            channel_ip=args.hololink)
    except Exception:
        proc.terminate()
        raise
    hololink_channel = hololink_module.DataChannel(channel_metadata)
    camera = hololink_module.sensors.vb1940.Vb1940Cam(hololink_channel)
    camera_mode = hololink_module.sensors.vb1940.Vb1940_Mode(args.camera_mode)

    app = WorkerApp(cu_context, 0, hololink_channel, camera, camera_mode,
                    args.shm_path, fps)

    hololink = hololink_channel.hololink()
    hololink.start()
    try:
        if args.reset:
            hololink.reset()
        proc.terminate()
        hololink.write_uint32(0x8, 0x0)
        if not args.skip_setup_clock:
            camera.setup_clock()
        hololink.write_uint32(0x8, 0x1)
        time.sleep(0.1)
        camera.get_register_32(0x0000)
        camera.get_register_32(0x0734)
        camera.configure(camera_mode)
        app.run()
    finally:
        hololink.stop()

    (cu_result,) = cuda.cuDevicePrimaryCtxRelease(cu_device)
    logging.info("worker exiting")


if __name__ == "__main__":
    main()
