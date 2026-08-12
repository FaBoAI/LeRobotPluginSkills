# SPDX-License-Identifier: Apache-2.0
from dataclasses import dataclass
from enum import Enum

from lerobot.cameras.configs import CameraConfig, ColorMode, Cv2Rotation


class ColorFormat(str, Enum):
    """カラーストリームの取得フォーマット。

    - AUTO: RGB (無圧縮) を優先し、無ければ MJPG → YUYV の順で選択
    - RGB:  無圧縮 RGB8 (Gemini 305 の 1280x800/1280x720 は 30fps まで。
            848x530 以下は 60fps も可)
    - MJPG: JPEG 圧縮 (1280x800/720 で 60fps が必要な場合はこれ。CPU でデコードする)
    - YUYV: YUV 4:2:2 (cv2 で変換)
    """

    AUTO = "auto"
    RGB = "rgb"
    MJPG = "mjpg"
    YUYV = "yuyv"

    @classmethod
    def _missing_(cls, value: object) -> None:
        raise ValueError(f"`color_format` is expected to be in {list(cls)}, but {value} is provided.")


@CameraConfig.register_subclass("gemini305")
@dataclass
class Gemini305CameraConfig(CameraConfig):
    """Orbbec Gemini 305 (USB ステレオデプスカメラ) の設定。

    pyorbbecsdk (Orbbec SDK v2 の Python バインディング) を直接使う。
    HSB プラグインと違いプロセス分離は不要 — pyorbbecsdk2 は manylinux aarch64 /
    Python 3.8〜3.13 のホイールを配布しており、LeRobot の環境にそのまま入る。

    例:
    ```python
    # 接続されている Gemini 305 が 1 台ならシリアル省略可
    Gemini305CameraConfig()                                   # デフォルト 848x530@30 RGB
    Gemini305CameraConfig(fps=30, width=1280, height=800)     # フル解像度
    Gemini305CameraConfig(fps=60, width=1280, height=800)     # 60fps (自動的に MJPG)
    Gemini305CameraConfig(serial_number_or_name="CV27561000LY", use_depth=True)
    ```

    Attributes:
        fps / width / height: カラーストリームの要求値。全部指定するか全部 None
            (None ならデバイスのデフォルトプロファイル。Gemini 305 の実機では
            848x530@30 — フル解像度が欲しければ 1280x800@30 を明示すること)。
        serial_number_or_name: シリアル番号 (例 "CV27561000LY") またはデバイス名。
            None なら接続されている Orbbec デバイスが 1 台のときだけ自動選択。
        color_mode: 出力画像の色順 (RGB / BGR)。デフォルト RGB。
        color_format: カラーストリームの取得フォーマット (auto/rgb/mjpg/yuyv)。
        use_rgb: カラーストリームを有効にする。デフォルト True。
        use_depth: デプスストリーム (Y16) を有効にする。デフォルト False。
            出力は (H, W, 1) uint16、単位 mm (SDK の depth scale を掛けて mm に正規化)。
        rotation: 画像回転 (0 / 90 / 180 / -90)。**270°回転は -90 と指定する**
            (LeRobot の Cv2Rotation は ROTATE_270 = -90。270 を渡すと ValueError)。
        warmup_s: connect() がフレーム安定まで待つ秒数 (最低 1 秒は強制)。

    Note:
        - depth は color と同じ解像度・fps で有効化される (Gemini 305 は
          カラーとデプスの両方が 1280x800@30 まで同一モードを持つ)。
        - depth の 60fps は 848x480 以下のみ。1280x800 の depth は 30fps まで。
    """

    serial_number_or_name: str | None = None
    color_mode: ColorMode = ColorMode.RGB
    color_format: ColorFormat = ColorFormat.AUTO
    use_rgb: bool = True
    use_depth: bool = False
    rotation: Cv2Rotation = Cv2Rotation.NO_ROTATION
    warmup_s: int = 1

    def __post_init__(self) -> None:
        self.color_mode = ColorMode(self.color_mode)
        self.color_format = ColorFormat(self.color_format)
        self.rotation = Cv2Rotation(self.rotation)

        if not self.use_rgb and not self.use_depth:
            raise ValueError("At least one of `use_rgb` or `use_depth` must be enabled.")

        values = (self.fps, self.width, self.height)
        if any(v is not None for v in values) and any(v is None for v in values):
            raise ValueError(
                "For `fps`, `width` and `height`, either all of them need to be set, or none of them."
            )
