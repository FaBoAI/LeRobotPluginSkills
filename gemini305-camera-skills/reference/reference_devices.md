# 対応 Orbbec カメラ デバイス別リファレンス

## Orbbec Gemini 305(type: `gemini305`)【実測済み 2026-08】

超小型(42×42×23mm, 68g)パッシブステレオデプスカメラ。ロボット手首搭載向け。
測距レンジ 4cm〜1m+、近距離でサブミリ精度。USB Type-C(USB 3.0 推奨 / 2.0 可)。

### 識別

| 項目 | 値 |
|---|---|
| USB VID:PID | `2bc5:0840`(VID 2bc5 = Orbbec 共通) |
| デバイス名 | `Orbbec Gemini 305` |
| シリアル形式 | 実測個体: `CV27561000LY`(英数字。`isdigit()` 前提のコードは書かない) |
| ファームウェア | 実測個体: 1.0.70 |
| UVC ノード | /dev/video0〜9 の 10 個(depth Z16 / IR GREY / IR BA81 / color YUYV ×2 + メタデータ) |
| SDK | pyorbbecsdk2 2.1.2(libOrbbecSDK 2.9.3)で動作確認 |

### カラーストリーム(実測。SDK 列挙で 226 プロファイル)

| 解像度 | fps | フォーマット |
|---|---|---|
| 1280×800 | 30/20/15/10/5 | RGB, BGR, RGBA, BGRA, YUYV, MJPG, Y16, Y8 |
| 1280×800 | **60** | **MJPG のみ** |
| 1280×720 | 30 以下は全フォーマット、**60 は MJPG のみ** | 同上 |
| 848×530 / 848×480 / 640×480 | **〜60(無圧縮含む)** | RGB/BGR/RGBA/BGRA/YUYV/MJPG/Y16/Y8 すべて 60fps あり |

- **無圧縮系 (RGB/YUYV) の 30fps 上限は 1280×800 / 1280×720 に限る**。
  848×530 以下は無圧縮 60fps が実在し実動する(実測 60.3fps @848×530 RGB)。
- **`get_default_video_stream_profile()` は 848×530@30** を返す(1280×800 ではない)。

### デプスストリーム(Y16)

| 解像度 | fps |
|---|---|
| 1280×800 / 1280×720 / 848×530 / 640×400 ほか | 30/20/15/10/5 |
| 848×480 / 640×480 / 424×240 / 320×240 | 上記 + **60** |

- **depth scale = 0.1mm/LSB**(`get_depth_scale()` 実測 0.1)。
  mm 規約に正規化するときは ×0.1 して uint16 に丸める。
- 有効画素率はシーン依存(白い天井向き実測: 1280×800 で 30〜53%、
  848×530 で 76〜77%)。パッシブステレオなのでテクスチャレス面で欠損する。

### 実測パフォーマンス(Jetson, USB3.2 接続)

| 構成 | 実測 fps |
|---|---|
| color 848×530@30 (デフォルト, RGB) | 30.14(10 秒平均) |
| color 1280×800@30 RGB + depth 1280×800@30 Y16 | 30.26 |
| color 1280×800@60 MJPG(CPU デコード込み) | 60.32 |
| color 848×530@60 RGB(無圧縮) | 60.32 |

### セットアップの癖

1. **udev ルール必須**: 未設置だと列挙は通るが open で
   `usbEnumerator openUsbDevice failed!`。ルールは pyorbbecsdk2 ホイール同梱
   (`site-packages/pyorbbecsdk/shared/99-obsensor-libusb.rules`)。
2. **PyPI の `pyorbbecsdk`(無印)は v1 系で使えない**。`pyorbbecsdk2` を入れる
   (import 名は同じ `pyorbbecsdk`)。
3. 再接続直後(pipeline stop → 即 start)は初回フレームが 1 秒超のことがある。
4. SDK はデフォルトで冗長なコンソールログを吐く →
   `Context.set_logger_level(OBLogLevel.ERROR)`。

### 診断コマンド

```bash
lsusb | grep 2bc5                          # 物理接続の確認 (2bc5:0840)
ls /etc/udev/rules.d/ | grep obsensor      # udev ルールの確認
lerobot-gemini305-monitor --duration 10    # 列挙 + fps 計測 (プラグイン同梱)
lerobot-gemini305-monitor --fps 30 --width 1280 --height 800 --depth --duration 10
```

## 新しい Orbbec カメラ(Gemini 335/336, Femto 等)を対応させる手順

pyorbbecsdk v2 は Gemini 330 シリーズ・Femto シリーズなど多数の機種を同一 API で
扱える。別機種の対応は:

1. **列挙の確認**: `Gemini305Camera.find_cameras()` 相当で PID・シリアルが見えるか。
   VID は 2bc5 で共通。プラグインは名前一致ではなく Orbbec デバイス全般を列挙する
   設計なので、多くの機種はそのまま動く可能性が高い。
2. **プロファイル実測**: 接続して COLOR/DEPTH の `get_stream_profile_list()` を
   ダンプし、モード表(解像度 × fps × フォーマット)と
   `get_default_video_stream_profile()` を本ファイルに記録する。
   **無圧縮フォーマットの fps 上限と depth scale は機種ごとに違う**
   (330 シリーズはアクティブステレオでレーザープロジェクタ制御プロパティがある)。
3. **type 名の判断**: 挙動が同じなら `gemini305` の Config に serial 指定で流用可。
   モード表・デフォルト・プロパティ(レーザー等)が違うなら別 type
   (例: `gemini335`)として Config を複製し、フェイクのモード表も実測値で作る。
4. **実測データの追記**: 動作確認したら本ファイルに識別情報・モード表・実測 fps・
   癖を追記する。
