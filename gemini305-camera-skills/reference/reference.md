# LeRobot 0.6.0 Gemini 305 カメラプラグイン実装知見

Jetson (aarch64, Python 3.13) + Orbbec Gemini 305 + LeRobot 0.6.0 +
pyorbbecsdk2 2.1.2 の実機構築(2026-08)で確認した内容。

## 1. カメラプラグイン検出の仕組みと命名規約

hsb-camera-skills の `reference.md` §1 と同一(配布名 `lerobot_camera_` プレフィックス
スキャン、`XxxConfig`→`Xxx` 解決、`__init__.py` re-export、
`@CameraConfig.register_subclass("gemini305")`)。要点のみ:

- 配布名 = トップレベルパッケージ名(アンダースコア維持)が必須。
- `lerobot-record` / `lerobot-teleoperate` は main() 冒頭で
  `register_third_party_plugins()` を呼ぶ → pip install 済みなら
  `--robot.cameras='{"wrist": {"type": "gemini305", ...}}'` がそのまま通る。
- **プラグインの import は pyorbbecsdk 無しでも成功しなければならない**。
  `register_third_party_plugins()` は配布名を無条件に import するため、
  `import pyorbbecsdk` を try/except で包み、実使用時(`__init__`)に
  ImportError を出す設計にする。

## 2. pyorbbecsdk の入手経路(最大の罠)

| パッケージ | 実体 | 使えるか |
|---|---|---|
| PyPI `pyorbbecsdk` | **v1 系** (1.3.2, 2024 年で停止)。x86_64 ホイールのみ | **不可**(Gemini 305 は v2 必須) |
| PyPI `pyorbbecsdk2` | **v2 系** (2.1.2, libOrbbecSDK 2.9.3 同梱) | **これを使う** |
| GitHub orbbec/pyorbbecsdk releases | v2 系 wheel (linux_aarch64 / manylinux) | 可(PyPI と同内容) |

- `pyorbbecsdk2` は **manylinux_2_27_aarch64 / cp38〜cp313** のホイールを配布。
  Jetson の Python 3.13(conda 環境)にもそのまま入る。**import 名はどちらも
  `pyorbbecsdk`** なので、間違えて無印を入れると気づきにくい。
- インストールは `pip install pyorbbecsdk2 --no-deps`(Jetson の CUDA torch/numpy
  スタック保護)。
- ネイティブライブラリ(libOrbbecSDK.so 2.9.3)と udev ルールはホイール同梱。
  追加の apt パッケージや SDK の別途インストールは不要。

## 3. udev ルール(初回セットアップで必須)

- 症状: `Context().query_devices()` の **enumeration は通る**(get_count()=1)のに、
  `get_device_by_index(0)` で `OBError: usbEnumerator openUsbDevice failed!`。
  列挙は USB ディスクリプタ読みだけで済むが、open には /dev/bus/usb への
  書き込み権限が要るため。
- 対処(ルールはホイール同梱):

```bash
sudo cp $(python -c 'import pyorbbecsdk, os; print(os.path.join(os.path.dirname(pyorbbecsdk.__file__), "shared", "99-obsensor-libusb.rules"))') /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

- 再プラグ不要(`udevadm trigger` で既存デバイスにも適用される)。
- プラグインはこのエラーを検知したら**上記コマンドをエラーメッセージに含める**。

## 4. RealSenseCamera 互換のスレッド設計(本プラグインの核)

LeRobot 組み込みの `lerobot/cameras/realsense/camera_realsense.py` を設計の
リファレンスにする(デプス付き USB カメラの公式実装)。同じにした点:

- バックグラウンドスレッドが `pipeline.wait_for_frames(1000)` をループし、
  `frame_lock` 下で `latest_color_frame` / `latest_depth_frame` /
  `latest_timestamp` を更新、`new_frame_event` を set。
- `read()` = event クリア後に新フレームを待つ(タイムアウト 10 秒)。
  `async_read(timeout_ms)` = event を待って latest を返す。
  `read_latest(max_age_ms)` = 待たずに latest を返す(古すぎたら TimeoutError)。
  depth 版 3 メソッド(`read_depth` / `async_read_depth` / `read_latest_depth`)も同形。
- depth 出力は **(H, W, 1) uint16、単位 mm**。
- エラー型: `DeviceAlreadyConnectedError` / `DeviceNotConnectedError`
  (`lerobot.utils.decorators` の `check_if_already_connected` /
  `check_if_not_connected` を使うと組み込みカメラと挙動が揃う)。

pyorbbecsdk 固有の差分:

- `wait_for_frames(timeout_ms)` は**タイムアウトで None を返す**(例外ではない)。
  None と OBError の両方をループ内で吸収する。
- FrameSet 内の color/depth は**片方だけ None のことがある**。来た方だけ更新し、
  new_frame_event は主ストリーム(use_rgb なら color)の更新時のみ set する。
- Context はプロセスで 1 つをモジュールレベルで共有(`find_cameras` と `connect`)。
  `Context.set_logger_level(OBLogLevel.ERROR)` で SDK のコンソールログを抑制する
  (デフォルトは毎フレームレベルのログで stdout が汚れる)。

## 5. プロファイル選択の落とし穴

1. **SDK のデフォルトプロファイルは 848×530@30**(1280×800 ではない)。
   `get_default_video_stream_profile()` の返す値を鵜呑みにした仕様書を書かないこと。
   フル解像度が欲しいユーザーには 1280×800@30 の明示を促す。
2. **1280×800 / 1280×720 の無圧縮 RGB/YUYV は 30fps まで。この 2 解像度の
   60fps は MJPG のみ**(848×530 以下は無圧縮 60fps が実在・実動する。
   実測 60.3fps @848×530 RGB — 「無圧縮は常に 30fps 上限」と一般化しないこと)。
   フォーマット自動選択(RGB→MJPG→YUYV)を入れると、1280×800 の `fps=60`
   指定だけで MJPG に自動フォールバックし、848×530@60 では無圧縮 RGB が選ばれる。
   MJPG は `cv2.imdecode`(BGR で返る→RGB 変換)。1280×800@60 で実測 60.3fps
   (Jetson、デコード込み)。
3. **解像度未指定 + use_depth の順序バグ**: デプスプロファイルを
   「任意(0,0,Y16,0)」で先に選ぶと、カラーのデフォルト 848×530 に対して
   1280×800 のデプスが選ばれる。デコード後の寸法検証で**全デプスフレームが
   静かに捨てられ、async_read_depth がタイムアウトする**。
   → カラープロファイル確定 → capture 寸法更新 → その寸法でデプス選択、の順にする。
4. `get_video_stream_profile(w, h, fmt, fps)` は一致プロファイルが無いと
   **OBError を投げる**(None ではない)。エラーメッセージには
   利用可能なプロファイル一覧の要約を入れる(実機は 226 プロファイルあるので
   重複除去した「WxH@fps fmt」形式で)。

## 6. depth の正規化

- Gemini 305 の `get_depth_scale()` は **0.1**(= raw 1 LSB が 0.1mm)。
  RealSense 互換の「uint16 = mm」規約に合わせるには
  `np.rint(raw.astype(np.float32) * scale).astype(np.uint16)`。
  0.1mm の精度は丸めで失われるが、ロボット学習用途では mm 規約の互換性を優先。
- 実測: 1280×800 depth の有効画素はシーン依存で 30〜100%
  (パッシブステレオなのでテクスチャレス面・近すぎる面で欠損する。
  白い天井に向けた実測で 52〜77%)。

## 7. 実機でしか出なかったバグ(教訓)

1. **`read_latest_depth` がカラーフレームを返す**: `_read_latest(max_age_ms)` に
   `read_depth=True` を渡し忘れ。ハードウェア不要テストは read_depth /
   async_read_depth しか叩いておらず素通しだった。**モニタ CLI の実機実行で
   `depth=(800,1280,3)` と表示されて発覚**。depth 系 3 メソッドすべてに
   テストを書くこと。
2. **再接続直後の初回フレーム遅延**: 連続で pipeline start/stop すると
   初回フレームが 1 秒を超えることがあり、warmup(1 秒)の `async_read` が
   TimeoutError で connect ごと失敗した。→ warmup とは別に
   初回フレーム猶予(10 秒)を設け、猶予内の TimeoutError は握りつぶす。
   猶予超過時はスレッドと pipeline を後始末してから ConnectionError。
3. **フェイクは実測に合わせる**: フェイクのデフォルトプロファイルを
   1280×800 にしていたら §5-3 の順序バグをテストで検出できなかった。
   実機の癖(デフォルト 848×530、RGB 30fps 上限)をフェイクに写経すること。

## 8. テスト戦略

- conftest.py で `sys.modules["pyorbbecsdk"] = fake_pyorbbecsdk` を
  **プラグイン import より先に**実行 → 実 SDK が入った環境でも常にフェイクで
  決定的にテストできる(プラグイン module は import 時に `ob` を束縛するため、
  注入は必ず先)。
- フェイクの制御点は 3 つで足りる: `devices`(接続台数・シリアル)、
  `fail_open`(udev 権限エラー)、`freeze`(フレーム停止 = タイムアウト系)。
- freeze 系のテストは**飛行中のフレーム 1 枚**を考慮する(freeze を立てた時点で
  wait_for_frames が 1 回分返すことがある → 1 枚消費してから TimeoutError を検証)。

## 9. 運用ノウハウ(Jetson / USB)

- USB3 接続を確認(`find_cameras()` の `connection_type` == "USB3.2")。
  USB2 でも動くが高解像度でフレームレートが落ちる。
- インストールは `pip install -e . --no-deps` + `pip install pyorbbecsdk2 --no-deps`。
- cwd の罠(hsb と同じ): プロジェクトの親ディレクトリを cwd にすると
  editable install が名前空間パッケージに隠される。
- 他プロセスが使用中のカメラは `Pipeline.start` か device open で OBError になる。
  `find_cameras()` は open せずに列挙する設計にしてある(使用中でも一覧に出る)。
