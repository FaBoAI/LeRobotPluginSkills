# SPDX-License-Identifier: Apache-2.0
"""テスト用フェイクワーカー — hsb_worker.py と同じ共有メモリプロトコルで
合成フレーム (動くグラデーション) を配信する。ハードウェア不要。"""

import argparse
import mmap
import signal
import struct
import sys
import time

import numpy as np

HEADER_FMT = "<IIIIIIQd"
HEADER_SIZE = 64
MAGIC = 0x48534243

MODE_TABLE = {
    0: (2560, 1984, 30),
    1: (1920, 1080, 30),
    2: (2560, 1984, 60),
    3: (2560, 1984, 30),
}

_running = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hololink", default="192.168.0.2")
    parser.add_argument("--camera-mode", type=int, default=1)
    parser.add_argument("--shm-path", required=True)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--skip-setup-clock", action="store_true")
    parser.add_argument("--fail", action="store_true", help="接続失敗をシミュレート")
    parser.add_argument("--fps-override", type=float, default=None)
    args = parser.parse_args()

    if args.fail:
        print("simulated enumeration failure", file=sys.stderr)
        sys.exit(1)

    def stop(*_):
        global _running
        _running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    w, h, fps = MODE_TABLE[args.camera_mode]
    if args.fps_override:
        fps = args.fps_override
    frame_bytes = w * h * 3

    with open(args.shm_path, "wb") as f:
        f.truncate(HEADER_SIZE + frame_bytes)
    f = open(args.shm_path, "r+b")
    mm = mmap.mmap(f.fileno(), HEADER_SIZE + frame_bytes)

    base = np.zeros((h, w, 3), dtype=np.uint8)
    base[..., 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    base[..., 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]

    seq = 0
    n = 0
    announced = False
    while _running:
        frame = base.copy()
        frame[..., 2] = (n * 7) % 256  # フレームごとに変化
        seq += 1
        struct.pack_into(HEADER_FMT, mm, 0, MAGIC, 1, w, h, 3, int(fps), seq, 0.0)
        mm[HEADER_SIZE:HEADER_SIZE + frame_bytes] = frame.tobytes()
        seq += 1
        struct.pack_into(HEADER_FMT, mm, 0, MAGIC, 1, w, h, 3, int(fps), seq,
                         time.time())
        if not announced:
            announced = True
            print(f"READY {w} {h} {int(fps)}", flush=True)
        n += 1
        time.sleep(1.0 / fps)


if __name__ == "__main__":
    main()
