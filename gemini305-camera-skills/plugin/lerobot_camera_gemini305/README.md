# lerobot_camera_gemini305

[Orbbec Gemini 305](https://www.orbbec.com/gemini-305/)(超小型ステレオデプスカメラ、
ロボット手首搭載向け)を [LeRobot](https://github.com/huggingface/lerobot) 0.6.x の
カメラプラグインとして使うためのパッケージ。

```bash
lerobot-record \
    --robot.type=so101_follower \
    --robot.cameras='{"wrist": {"type": "gemini305"}}' \
    ...
```

## 特徴

- **pyorbbecsdk (Orbbec SDK v2) を直接使用** — HSB プラグインのようなプロセス分離は
  不要。`pyorbbecsdk2` は manylinux aarch64 / cp38〜cp313 のホイールを PyPI で配布して
  おり、LeRobot の環境(Jetson の Python 3.13 含む)にそのまま入る。
- **LeRobot 組み込みの RealSenseCamera と同じ規約** — `read()` / `async_read()` /
  `read_latest()` / `read_depth()` / `async_read_depth()` / `read_latest_depth()`。
  depth は `(H, W, 1)` uint16、単位 mm。
- **カラーフォーマット自動選択** — 無圧縮 RGB を優先し、無圧縮が無いモード
  (1280×800/720 の 60fps)では MJPG に自動フォールバック(CPU デコード)。
- シリアル番号でのデバイス選択(1 台構成なら省略可)、回転(90/180/270)、
  BGR 出力に対応。

## インストール

```bash
# Jetson では既存の CUDA torch/numpy スタックを守るため --no-deps 推奨
pip install pyorbbecsdk2 --no-deps
cd lerobot_camera_gemini305
pip install -e . --no-deps
```

**注意**: PyPI の `pyorbbecsdk`(無印)は v1 系・x86_64 のみで Gemini 305 に使えない。
必ず `pyorbbecsdk2` を入れること(import 名はどちらも `pyorbbecsdk`)。

### udev ルール(初回のみ必須)

SDK からの USB アクセスには udev ルールが必要(無いと
`usbEnumerator openUsbDevice failed!` になる):

```bash
sudo cp $(python -c 'import pyorbbecsdk, os; print(os.path.join(os.path.dirname(pyorbbecsdk.__file__), "shared", "99-obsensor-libusb.rules"))') /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## 動作確認

```bash
# デバイス列挙 + 10 秒間の fps 計測
lerobot-gemini305-monitor --duration 10

# デプスも有効にして計測
lerobot-gemini305-monitor --depth --duration 10

# 60fps (MJPG)
lerobot-gemini305-monitor --fps 60 --width 1280 --height 800 --duration 10
```

## 設定 (`Gemini305CameraConfig`)

| フィールド | デフォルト | 説明 |
|---|---|---|
| `fps` / `width` / `height` | None | 全部指定するか全部省略(省略時はデバイスデフォルトの 848×530@30。フル解像度は 1280×800@30 を明示) |
| `serial_number_or_name` | None | 1 台構成なら省略可。複数台はシリアル必須 |
| `color_mode` | `rgb` | 出力の色順(`rgb` / `bgr`) |
| `color_format` | `auto` | `auto`(RGB→MJPG→YUYV の順で選択)/ `rgb` / `mjpg` / `yuyv` |
| `use_rgb` | True | カラーストリーム |
| `use_depth` | False | デプスストリーム(Y16、出力は mm 単位 uint16) |
| `rotation` | 0 | 0 / 90 / 180 / -90(270°回転は **-90** と指定。270 はエラー) |
| `warmup_s` | 1 | connect 時のウォームアップ秒数(最低 1 秒) |

### 使用例(Python)

```python
from lerobot_camera_gemini305 import Gemini305Camera, Gemini305CameraConfig

cam = Gemini305Camera(
    Gemini305CameraConfig(fps=30, width=1280, height=800, use_depth=True)
)
cam.connect()
color = cam.async_read()        # (800, 1280, 3) uint8 RGB
depth = cam.async_read_depth()  # (800, 1280, 1) uint16 [mm]
cam.disconnect()
```

## 対応モード(Gemini 305 実測)

- カラー: 1280×800 / 1280×720 / 848×530 / 640×480 ほか。
  **1280×800 / 1280×720** の無圧縮 RGB・YUYV は **30fps まで**で、
  この 2 解像度の **60fps は MJPG のみ**。**848×530 以下は無圧縮 60fps も可**
  (実測 60.3fps @848×530 RGB)。
- デプス: Y16。1280×800@30 まで(60fps は 848×480 以下)。
  depth scale は 0.1mm/LSB(本プラグインが mm に正規化)。

## テスト(ハードウェア不要)

```bash
python -m pytest tests/ -v
```

`tests/fake_pyorbbecsdk.py`(合成フレームを返すフェイク SDK)を `sys.modules` に
注入するため、実カメラなしで全ロジックが検証できる。

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `openUsbDevice failed!` | udev ルール未設置(上記参照) |
| `No Orbbec device found` | USB 接続を確認。`lsusb \| grep 2bc5` で見えるか |
| fps が出ない | USB3 ポート接続か確認(`find_cameras` の `connection_type` が `USB3.2` か)。USB2 だと帯域不足 |
| `no color profile for ...@60` | 1280×800/720 の 60fps は MJPG のみ(auto なら自動選択)。848×530 以下なら無圧縮 60fps 可 |
| 他プロセスが使用中 | `Failed to open` / start 失敗。使用中のプロセスを止める |
