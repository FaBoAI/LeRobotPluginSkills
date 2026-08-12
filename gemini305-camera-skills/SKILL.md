---
name: gemini305-camera-skills
description: Orbbec Gemini 305 (USB ステレオデプスカメラ) を LeRobot 0.6.x のカメラプラグインとして対応させるコード生成・動作確認を実施するスキル。Jetson (aarch64 / Python 3.13) + pyorbbecsdk v2 の実機構築で検証済みの手順と制約に基づく。
---

# 概要

USB 接続した Orbbec Gemini 305(超小型ステレオデプスカメラ、ロボット手首搭載向け)を、
LeRobot のカメラ(`--robot.cameras='{"wrist": {"type": "gemini305", ...}}'`)として
使うサードパーティプラグインを生成し、実機での動作確認まで実施する。
本スキルは Gemini 305 + Jetson (aarch64, Python 3.13) + LeRobot 0.6.0 +
pyorbbecsdk2 2.1.2 の実機構築(2026-08)で検証済みの知見に基づく。

# 実装前に必ず参照する

- 実装知見(SDK 直接統合の設計・LeRobot カメラ規約・落とし穴): `./reference/reference.md`
- **対応カメラのデバイス別特性(モード表・識別・実測値)**: `./reference/reference_devices.md`
- **実機検証済みの完全な実装(リファレンス)**: `./plugin/lerobot_camera_gemini305/`
  — そのまま `pip install -e . --no-deps` で使用可能。新環境ではまずこれを試し、
  差分が必要な場合のみコード生成する

# 前提知識(コード生成前に必ず理解すること)

1. **HSB と違いプロセス分離は不要**: pyorbbecsdk v2 は PyPI パッケージ名
   `pyorbbecsdk2` として manylinux aarch64 / cp38〜cp313 のホイールを配布しており、
   LeRobot の環境(Jetson の Python 3.13 含む)に直接インストールできる。
   **PyPI の `pyorbbecsdk`(無印)は v1 系・x86_64 のみで使えない罠**
   (import 名はどちらも `pyorbbecsdk`)。
2. **カメラプラグイン規約**: 配布パッケージ名は `lerobot_camera_` プレフィックス必須
   (発見は配布名プレフィックススキャン)。`XxxConfig`→`Xxx` の命名、
   `__init__.py` で両クラス re-export、`@CameraConfig.register_subclass("type名")`。
   詳細は `reference.md` §1。
3. **組み込み RealSenseCamera の規約に合わせる**: LeRobot 0.6.0 の
   `lerobot/cameras/realsense/` はデプス付き USB カメラの公式リファレンス。
   スレッド設計(バックグラウンド読み + frame_lock + new_frame_event)と
   API(`read`/`async_read`/`read_latest` + `_depth` 系)をそのまま踏襲すると、
   下流ツールが同じ挙動を期待できる。depth 出力は **(H, W, 1) uint16、単位 mm**。
4. **udev ルールが必須**: 無いと enumeration は通るが open で
   `usbEnumerator openUsbDevice failed!` になる。ルールファイルは
   pyorbbecsdk2 ホイール同梱(`site-packages/pyorbbecsdk/shared/`)。
5. **フォーマットの制約(Gemini 305)**: **1280×800 / 1280×720** の無圧縮 RGB は
   30fps まで、この 2 解像度の **60fps は MJPG のみ**(CPU デコードが必要)。
   **848×530 以下は無圧縮 60fps も可**(実測 60.3fps @848×530 RGB)。
   SDK のデフォルトプロファイルは 1280×800 ではなく **848×530@30**。

# ワークフロー

## Step 1: カメラ疎通確認

```bash
lsusb | grep 2bc5        # Orbbec VID。Gemini 305 は 2bc5:0840
ls /dev/video*           # UVC ノードが 10 個生える (video0-9)
```

- `lsusb` で見えるのに SDK で開けない → udev ルール未設置(Step 2)。
- USB3 接続を確認(`find_cameras()` の `connection_type` が `USB3.2`)。
  USB2 だと高解像度で帯域不足になる。

## Step 2: pyorbbecsdk 実行環境の確認

```bash
pip install pyorbbecsdk2 --no-deps    # LeRobot と同じ環境に入れる
python -c "from pyorbbecsdk import Context; print(Context().query_devices().get_count())"
```

- `1` 以上が出れば enumeration OK。デバイス open まで確認するには
  `lerobot-gemini305-monitor`(プラグインの診断 CLI)を使う。
- `openUsbDevice failed!` → udev ルールを設置:

```bash
sudo cp $(python -c 'import pyorbbecsdk, os; print(os.path.join(os.path.dirname(pyorbbecsdk.__file__), "shared", "99-obsensor-libusb.rules"))') /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

- **`--no-deps` 必須(Jetson)**: pip の依存解決が numpy 等をダウングレードし
  CUDA ビルドの torch スタックを壊すことがある。

## Step 3: コード生成

パッケージ構成(検証済みの形):

```
lerobot_camera_gemini305/
├── pyproject.toml                       # name = "lerobot_camera_gemini305" (発見に必須)
├── README.md
├── lerobot_camera_gemini305/
│   ├── __init__.py                      # Gemini305CameraConfig / Gemini305Camera を re-export
│   ├── configuration_gemini305.py       # @CameraConfig.register_subclass("gemini305")
│   ├── camera_gemini305.py              # Camera 実装 (pyorbbecsdk 直接 + 読み取りスレッド)
│   └── monitor.py                       # 診断 CLI (lerobot-gemini305-monitor)
└── tests/
    ├── conftest.py                      # フェイク SDK を sys.modules に注入
    ├── fake_pyorbbecsdk.py              # 合成フレームを返すフェイク SDK
    └── test_camera.py                   # ハードウェア不要の pytest
```

実装の要点(詳細は `reference.md`):

- Config は RealSenseCameraConfig に準拠(`serial_number_or_name` は 1 台構成なら
  省略可に緩和)。fps/width/height は「全部指定 or 全部省略」を `__post_init__` で検証。
- カラーフォーマットは AUTO(RGB→MJPG→YUYV の優先順)で選択。
  MJPG は `cv2.imdecode`、YUYV は `cv2.cvtColor(COLOR_YUV2RGB_YUYV)` でデコード。
- **解像度未指定 + use_depth の順序**: カラープロファイルを確定して capture 寸法を
  更新した**後に**デプスプロファイルを選ぶ(先に選ぶと 848×530 のカラーに対し
  1280×800 のデプスが選ばれ、寸法検証で全デプスフレームが捨てられる)。
- depth は SDK の `get_depth_scale()`(Gemini 305 は 0.1mm/LSB)を掛けて
  **mm 単位の uint16** に正規化する。
- `connect(warmup=True)` は warmup_s とは別に**初回フレーム猶予(10 秒)**を持つ。
  再接続直後は初回フレームが 1 秒を超えることがある(実測)。
- 失敗パスのエラーメッセージに対処コマンドを含める(udev / USB3 / 使用中)。

### 実装上の制約(ハマりやすい点。必ず守ること)

- `Camera` サブクラスに `config_class` と `name = "gemini305"` のクラス変数。
- `import pyorbbecsdk` は try/except で包む(未インストール環境で
  `register_third_party_plugins()` の import を壊さない。使用時に ImportError)。
- 配布名 = パッケージ名。プロジェクトの親ディレクトリを cwd にして python を
  起動しない(名前空間パッケージが editable install を隠す)。
- `_read_latest` / `_async_read` 系の**深度版は `read_depth=True` の渡し忘れに注意**
  (カラーフレームが返る。実機のモニタ CLI で発覚した実バグ — テストに回帰済み)。
- `pyproject.toml` の `build-backend` は `setuptools.build_meta`。

## Step 4: ハードウェア不要テスト

- `fake_pyorbbecsdk.py`(合成フレームを返すフェイク SDK)を conftest.py が
  `sys.modules["pyorbbecsdk"]` に注入 → 実 SDK がある環境でも決定的にテストできる。
- カバーする項目: 登録(`CameraConfig.get_choice_class("gemini305")`)、
  `make_cameras_from_configs` からのクラス解決、connect/read/async_read/
  read_latest/disconnect、depth 系 3 メソッド、**depth とカラーの解像度一致**、
  BGR 変換、回転、フォーマット選択(RGB@30 / MJPG@60 / YUYV)、タイムアウト、
  デバイス選択(0 台・複数台・シリアル指定・不一致)、udev エラーメッセージ、
  二重接続、切断後 read、再接続。
- フェイクのモード表・デフォルトプロファイルは**実測値に合わせる**
  (デフォルト 848×530@30 — ここを 1280×800 にしていたせいで
  実機バグを 1 つ見逃しかけた)。

## Step 5: インストールと実機確認

### インストール(確定コマンド)

```bash
pip install pyorbbecsdk2 --no-deps
cd lerobot_camera_gemini305
pip install -e . --no-deps
```

### 実機確認(確定コマンド)

```bash
lerobot-gemini305-monitor --duration 10                                   # デフォルト 848x530@30
lerobot-gemini305-monitor --fps 30 --width 1280 --height 800 --depth --duration 10
lerobot-gemini305-monitor --fps 60 --width 1280 --height 800 --duration 10  # MJPG 自動選択
```

- 期待値: `devices:` にシリアルと `USB3.2` が出る → 30fps / 60fps が安定
  (実測: 30.26fps @1280×800 color+depth、60.32fps @MJPG)。
- depth の `valid=..%` はシーン依存(パッシブステレオはテクスチャが無いと欠損する)。

## Step 6: lerobot-record 統合

```bash
lerobot-record \
    --robot.type=so101_follower \
    --robot.port=<ポート> \
    --robot.cameras='{"wrist": {"type": "gemini305", "fps": 30, "width": 1280, "height": 800}}' \
    ...
```

- 収録は 1280×800@30(無圧縮 RGB)が扱いやすい。60fps が必要なら
  848×530@60(無圧縮 RGB が自動選択)か、1280×800@60(MJPG 自動選択 —
  CPU デコード負荷に注意)を選ぶ。
- 縮小が必要なら LeRobot 側の image transform を使う(プラグインはリサイズしない)。
- depth を dataset に入れる運用は LeRobot 側の対応状況に依存する
  (カメラ API としては `async_read_depth()` で取得可能)。

## Step 7: 知見の記録

- 新たな知見(未対応 Orbbec カメラの追加、USB 帯域の癖など)は
  `./reference/reference.md` / `./reference/reference_devices.md` に追記する。
- プラグインの README.md を source of truth として扱い、コードと docs を一致させる。
- 診断 CLI(monitor)を必ず同梱する。**実機でしか出ないバグ(read_latest_depth の
  取り違え・warmup transient)はモニタ CLI が最初に暴いた** — カメラ側の物理問題
  (USB/udev)とプラグインの問題の切り分けにも必須。
