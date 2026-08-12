# SPDX-License-Identifier: Apache-2.0
"""lerobot-hsb-monitor — HSBCamera の診断 CLI。

ワーカーを起動してフレームレート・レイテンシ・リンク状態を表示する。
LeRobot 本体なしでカメラ単体の健全性を確認する用途。
"""

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="HSB camera diagnostic monitor")
    parser.add_argument("--hololink", default="192.168.0.2")
    parser.add_argument("--camera-mode", type=int, default=0)
    parser.add_argument("--duration", type=float, default=10.0,
                        help="計測時間 (秒)")
    args = parser.parse_args()

    # carrier の事前チェック
    try:
        with open("/sys/class/net/mgbe0_0/carrier") as f:
            carrier = f.read().strip()
        print(f"mgbe0_0 carrier: {carrier} ({'link OK' if carrier == '1' else 'リンクなし!'})")
    except OSError:
        print("mgbe0_0: interface not found")

    from .camera_hsb import HSBCamera
    from .configuration_hsb import HSBCameraConfig

    found = HSBCamera.find_cameras()
    print(f"enumeration broadcast: {found if found else 'なし (ワーカー起動中なら正常)'}")

    cfg = HSBCameraConfig(camera_mode=args.camera_mode, hololink_ip=args.hololink)
    cam = HSBCamera(cfg)
    print(f"connecting {cam} ...")
    cam.connect(warmup=False)
    try:
        n = 0
        t0 = time.monotonic()
        last_report = t0
        while time.monotonic() - t0 < args.duration:
            frame = cam.async_read(timeout_ms=2000)
            n += 1
            now = time.monotonic()
            if now - last_report >= 1.0:
                print(f"  {n / (now - t0):6.2f} fps  shape={frame.shape} dtype={frame.dtype}")
                last_report = now
        print(f"total {n} frames in {time.monotonic() - t0:.1f}s")
    finally:
        cam.disconnect()
    print("OK")


if __name__ == "__main__":
    main()
