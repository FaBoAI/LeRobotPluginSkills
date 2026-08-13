# SPDX-License-Identifier: Apache-2.0
"""HSBCamera — Holoscan Sensor Bridge (VB1940 Eagle) の LeRobot カメラ実装。

アーキテクチャ:
    [LeRobot プロセス]                     [hsb venv (Python 3.12) ワーカー]
    HSBCamera.connect() ── subprocess ──▶ hsb_worker.py
         ▲                                   │ hololink → holoscan パイプライン
         └── /dev/shm seqlock バッファ ◀──── │ RGB8 フレームを書き続ける

hololink/holoscan は特定の Python/numpy に固定されているため、LeRobot 側の
環境に依存しないようプロセスを分離し、共有メモリでフレームを受け渡す。
"""

import collections
import logging
import mmap
import os
import select
import socket
import struct
import subprocess
import threading
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from lerobot.cameras.camera import Camera
from lerobot.cameras.configs import ColorMode
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from .configuration_hsb import MODE_TABLE, HSBCameraConfig

logger = logging.getLogger(__name__)

HEADER_FMT = "<IIIIIIQd"  # magic, version, w, h, ch, fps, seq, ts
HEADER_SIZE = 64
MAGIC = 0x48534243  # "HSBC"

HOLOSCAN_PY = "/opt/nvidia/holoscan/python/lib"
HOLOSCAN_LIB = "/opt/nvidia/holoscan/lib"
NVIDIA_LIB = "/usr/lib/aarch64-linux-gnu/nvidia"
CUDA_BIN = "/usr/local/cuda/bin"

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsb_worker.py")


class HSBCamera(Camera):
    config_class = HSBCameraConfig
    name = "hsb"

    def __init__(self, config: HSBCameraConfig):
        super().__init__(config)  # self.fps/width/height を設定
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._mm: mmap.mmap | None = None
        self._file = None
        self._shm_path: str | None = None
        self._last_seq = 0
        self._stderr_tail: collections.deque = collections.deque(maxlen=40)
        self._drain_threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return (
            self._proc is not None
            and self._proc.poll() is None
            and self._mm is not None
        )

    @staticmethod
    def find_cameras() -> list[dict[str, Any]]:
        """カメラの BOOTP enumeration ブロードキャスト (UDP 12267) を待ち受けて検出する。"""
        found: dict[str, dict[str, Any]] = {}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", 12267))
            s.settimeout(0.5)
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                try:
                    data, addr = s.recvfrom(2048)
                except socket.timeout:
                    continue
                mac = ":".join(f"{b:02x}" for b in data[28:34]) if len(data) > 34 else "?"
                found[addr[0]] = {"type": "hsb", "ip": addr[0], "mac": mac}
            s.close()
        except OSError:
            pass  # ポート使用中 (ワーカー動作中など) は検出スキップ
        return list(found.values())

    # ------------------------------------------------------------------ #
    def connect(self, warmup: bool = True) -> None:
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{self} is already connected.")

        cfg = self.config
        self._shm_path = os.path.join(
            cfg.shm_dir, f"lerobot_hsb_{os.getpid()}_{id(self):x}"
        )

        venv_bin = os.path.dirname(os.path.abspath(cfg.hsb_python))
        env = {
            "HOME": os.environ.get("HOME", "/tmp"),
            "PATH": f"{venv_bin}:{CUDA_BIN}:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": HOLOSCAN_PY,
            "LD_LIBRARY_PATH": f"{HOLOSCAN_LIB}:{NVIDIA_LIB}",
        }
        cmd = [
            cfg.hsb_python, cfg.worker_script or _WORKER,
            "--hololink", cfg.hololink_ip,
            "--camera-mode", str(cfg.camera_mode),
            "--shm-path", self._shm_path,
        ]
        if cfg.reset:
            cmd.append("--reset")
        if cfg.skip_setup_clock:
            cmd.append("--skip-setup-clock")
        if cfg.exposure is not None:
            cmd += ["--exposure", str(cfg.exposure)]
        if cfg.analog_gain is not None:
            cmd += ["--analog-gain", str(cfg.analog_gain)]
        native_w, native_h, _ = MODE_TABLE[cfg.camera_mode]
        if (cfg.width, cfg.height) != (native_w, native_h):
            cmd += ["--out-width", str(cfg.width), "--out-height", str(cfg.height)]

        logger.info("starting HSB worker: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # stderr は常時ドレインして直近を保持 (エラー報告用)
        t = threading.Thread(target=self._drain, args=(proc.stderr,), daemon=True)
        t.start()
        self._drain_threads = [t]

        # READY ハンドシェイク (最初のフレームが共有メモリに書かれた合図)
        ready = None
        deadline = time.monotonic() + cfg.connect_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            r, _, _ = select.select([proc.stdout], [], [], 0.5)
            if r:
                line = proc.stdout.readline()
                if line.startswith("READY"):
                    ready = line.split()
                    break
        if ready is None:
            tail = "\n".join(self._stderr_tail)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._cleanup_shm()
            raise ConnectionError(
                f"HSB worker did not become ready in {cfg.connect_timeout_s}s "
                f"(camera {cfg.hololink_ip}).\n"
                "確認: 1) リンク (cat /sys/class/net/mgbe0_0/carrier == 1)  "
                "2) カメラの冷却  3) リンク断なら sudo bash "
                "/home/jetson/camera/bounce_and_capture.sh 相当の down/up。\n"
                f"worker stderr:\n{tail}"
            )

        w, h = int(ready[1]), int(ready[2])
        if (w, h) != (self.width, self.height):
            proc.terminate()
            raise ConnectionError(
                f"worker frame size {w}x{h} != configured {self.width}x{self.height}"
            )

        # stdout も以後ドレイン (ブロック防止)
        t2 = threading.Thread(target=self._drain, args=(proc.stdout,), daemon=True)
        t2.start()
        self._drain_threads.append(t2)

        frame_bytes = w * h * 3
        self._file = open(self._shm_path, "rb")
        self._mm = mmap.mmap(
            self._file.fileno(), HEADER_SIZE + frame_bytes, prot=mmap.PROT_READ
        )
        magic = struct.unpack_from("<I", self._mm, 0)[0]
        if magic != MAGIC:
            proc.terminate()
            raise ConnectionError(f"bad shm magic: {magic:#x}")

        self._proc = proc
        self._last_seq = 0
        if warmup:
            time.sleep(cfg.warmup_s)
        logger.info("HSBCamera connected: %dx%d@%d via %s", w, h, self.fps, cfg.hololink_ip)

    def _drain(self, stream) -> None:
        try:
            for line in stream:
                line = line.rstrip()
                if line:
                    self._stderr_tail.append(line)
        except (ValueError, OSError):
            pass

    def _cleanup_shm(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._shm_path and os.path.exists(self._shm_path):
            try:
                os.unlink(self._shm_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    def _snapshot(self) -> tuple[int, float, NDArray[Any]] | None:
        """seqlock 読み: (seq, ts, frame) を返す。安定読みできなければ None。"""
        mm = self._mm
        for _ in range(64):
            _, _, w, h, ch, _, seq1, ts = struct.unpack_from(HEADER_FMT, mm, 0)
            if seq1 == 0:  # まだ 1 フレームも書かれていない
                return None
            if seq1 & 1:  # 書き込み中
                continue
            buf = bytes(mm[HEADER_SIZE:HEADER_SIZE + w * h * ch])
            seq2 = struct.unpack_from("<Q", mm, 24)[0]
            if seq1 == seq2:
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, ch)
                return seq1, ts, frame
        return None

    def _postprocess(self, frame: NDArray[Any], color_mode: ColorMode | None) -> NDArray[Any]:
        mode = ColorMode(color_mode) if color_mode is not None else self.config.color_mode
        if mode == ColorMode.BGR:
            return np.ascontiguousarray(frame[..., ::-1])
        return frame

    def read(self, color_mode: ColorMode | None = None) -> NDArray[Any]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        snap = self._snapshot()
        if snap is None:
            raise RuntimeError("HSBCamera: no frame available yet")
        seq, _, frame = snap
        self._last_seq = seq
        return self._postprocess(frame, color_mode)

    def async_read(self, timeout_ms: float = 200) -> NDArray[Any]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            snap = self._snapshot()
            if snap is not None and snap[0] != self._last_seq:
                self._last_seq = snap[0]
                return self._postprocess(snap[2], None)
            if self._proc is not None and self._proc.poll() is not None:
                tail = "\n".join(self._stderr_tail)
                raise RuntimeError(f"HSB worker died:\n{tail}")
            time.sleep(0.001)
        raise TimeoutError(
            f"HSBCamera: no new frame within {timeout_ms}ms "
            "(リンク断の可能性 — カメラの冷却と carrier を確認)"
        )

    def read_latest(self, max_age_ms: int = 500) -> NDArray[Any]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        snap = self._snapshot()
        if snap is not None:
            seq, ts, frame = snap
            if ts > 0 and (time.time() - ts) * 1000.0 <= max_age_ms:
                self._last_seq = seq
                return self._postprocess(frame, None)
        return self.async_read()

    # ------------------------------------------------------------------ #
    def disconnect(self) -> None:
        if self._proc is None and self._mm is None:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
            self._proc = None
        self._cleanup_shm()
        logger.info("HSBCamera disconnected")

    def __str__(self) -> str:
        return f"HSBCamera({self.config.hololink_ip}, mode={self.config.camera_mode})"
