# SPDX-License-Identifier: Apache-2.0
from dataclasses import dataclass

from lerobot.cameras.configs import CameraConfig, ColorMode

# VB1940 のモード表: mode -> (width, height, fps)
MODE_TABLE = {
    0: (2560, 1984, 30),
    1: (1920, 1080, 30),
    2: (2560, 1984, 60),
    3: (2560, 1984, 30),  # RAW8
}


@CameraConfig.register_subclass("hsb")
@dataclass
class HSBCameraConfig(CameraConfig):
    """Holoscan Sensor Bridge (VB1940 Eagle) カメラ設定。

    フレーム取得は hololink/holoscan が動く専用 venv のワーカープロセスが行い、
    本プラグインは /dev/shm 経由でフレームを受け取る (LeRobot 側の Python や
    numpy のバージョンと独立に動作する)。
    """

    # 継承フィールド (kw_only): fps, width, height — 未指定なら camera_mode から導出
    hololink_ip: str = "192.168.0.2"
    camera_mode: int = 0  # 0:2560x1984@30 1:1920x1080@30 2:2560x1984@60 3:RAW8
    color_mode: ColorMode = ColorMode.RGB
    warmup_s: float = 1.0
    # hololink/holoscan が入った venv の python (install_hsb.py が構築するもの)
    hsb_python: str = "/home/jetson/camera/venv/bin/python"
    # ワーカー起動から最初のフレームまでの許容時間 (enumeration + センサー設定を含む)
    connect_timeout_s: float = 90.0
    # hololink.reset() を発行するか (リンクが不安定な環境では False 推奨)
    reset: bool = False
    skip_setup_clock: bool = False
    shm_dir: str = "/dev/shm"
    # テスト/デバッグ用: ワーカースクリプトの差し替え (None = 同梱の hsb_worker.py)
    worker_script: str | None = None

    def __post_init__(self) -> None:
        self.color_mode = ColorMode(self.color_mode)
        if self.camera_mode not in MODE_TABLE:
            raise ValueError(
                f"camera_mode={self.camera_mode} は未対応 (0-3)。"
                f" 0:2560x1984@30 1:1920x1080@30 2:2560x1984@60 3:2560x1984@30(RAW8)"
            )
        w, h, fps = MODE_TABLE[self.camera_mode]
        if self.width is None:
            self.width = w
        if self.height is None:
            self.height = h
        if self.fps is None:
            self.fps = fps
        if (self.width, self.height) != (w, h):
            raise ValueError(
                f"width/height ({self.width}x{self.height}) が camera_mode "
                f"{self.camera_mode} の {w}x{h} と一致しません。"
                " リサイズが必要な場合は LeRobot 側の image transform を使ってください。"
            )
