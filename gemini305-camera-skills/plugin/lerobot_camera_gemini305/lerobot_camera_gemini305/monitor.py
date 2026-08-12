# SPDX-License-Identifier: Apache-2.0
"""lerobot-gemini305-monitor — Gemini305Camera の診断 CLI。

デバイス列挙とフレームレート計測を LeRobot 本体なしで行う。
カメラ側の問題 (USB 帯域・udev・使用中) とプラグインの問題の切り分け用。
"""

import argparse
import time

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description="Orbbec Gemini 305 diagnostic monitor")
    parser.add_argument("--serial", default=None, help="シリアル番号 (省略時は自動選択)")
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--color-format", default="auto", choices=["auto", "rgb", "mjpg", "yuyv"])
    parser.add_argument("--depth", action="store_true", help="デプスストリームも有効化して計測")
    parser.add_argument("--duration", type=float, default=10.0, help="計測時間 (秒)")
    args = parser.parse_args()

    from .camera_gemini305 import Gemini305Camera
    from .configuration_gemini305 import Gemini305CameraConfig

    found = Gemini305Camera.find_cameras()
    print(f"devices: {found if found else 'なし (USB 接続と udev ルールを確認)'}")
    if not found:
        raise SystemExit(1)

    cfg = Gemini305CameraConfig(
        serial_number_or_name=args.serial,
        fps=args.fps,
        width=args.width,
        height=args.height,
        color_format=args.color_format,
        use_depth=args.depth,
    )
    cam = Gemini305Camera(cfg)
    print(f"connecting {cam} ...")
    cam.connect(warmup=True)
    print(
        f"connected: {cam.width}x{cam.height}@{cam.fps} "
        f"color_format={cam._color_format_actual and cam._color_format_actual.value}"
    )
    try:
        n = 0
        t0 = time.monotonic()
        last_report = t0
        while time.monotonic() - t0 < args.duration:
            frame = cam.async_read(timeout_ms=2000)
            n += 1
            now = time.monotonic()
            if now - last_report >= 1.0:
                line = f"  {n / (now - t0):6.2f} fps  color={frame.shape} {frame.dtype}"
                if args.depth:
                    try:
                        d = cam.read_latest_depth(max_age_ms=2000)
                        valid = d[d > 0]
                        med = int(np.median(valid)) if valid.size else 0
                        line += f"  depth={d.shape} valid={100 * valid.size // d.size}% median={med}mm"
                    except (TimeoutError, RuntimeError) as e:
                        line += f"  depth=({e})"
                print(line)
                last_report = now
        print(f"total {n} frames in {time.monotonic() - t0:.1f}s "
              f"(avg {n / (time.monotonic() - t0):.2f} fps)")
    finally:
        cam.disconnect()
    print("OK")


if __name__ == "__main__":
    main()
