---
name: hsb-camera-skills
description: NVIDIA Holoscan Sensor Bridge 経由の10GigEカメラ(Leopard VB1940 Eagle等)をLeRobot 0.6.x のカメラプラグインとして対応させるコード生成・動作確認を実施するスキル。Jetson AGX Thor での実機構築(Dockerレス)で検証済みの手順と制約に基づく。
---

# 概要

Jetson AGX Thor の QSFP ポートに接続した Holoscan Sensor Bridge (HSB) カメラ
(Leopard Imaging VB1940 Eagle, 10GigE PoE)を、LeRobot のカメラ
(`--robot.cameras='{"front": {"type": "hsb", ...}}'`)として使うサードパーティ
プラグインを生成し、実機での動作確認まで実施する。
本スキルは VB1940 + Jetson AGX Thor (JetPack 7 / CUDA 13 / Holoscan SDK 3.6 deb) +
LeRobot 0.6.0 の実機構築(2026-08)で検証済みの知見に基づく。

# 実装前に必ず参照する

- 実装知見(プロセス分離ブリッジ設計・LeRobot カメラ規約・落とし穴): `./reference/reference.md`
- **対応カメラのデバイス別特性(モード表・enumeration・熱の癖)**: `./reference/reference_devices.md`
- **実機検証済みの完全な実装(リファレンス)**: `./plugin/lerobot_camera_hsb/`
  — そのまま `pip install -e . --no-deps` で使用可能。新環境ではまずこれを試し、
  差分が必要な場合のみコード生成する

# 前提知識(コード生成前に必ず理解すること)

1. **hololink スタックと LeRobot は同居できない**: hololink の Python バインディングは
   Holoscan SDK deb の Python バージョン(cpython-312)に固定され、公式デモは
   numpy==1.26.0 を要求する。一方 LeRobot 0.6.0 は numpy>=2.0 を要求する。
   したがってプラグインは**プロセス分離**が必須: hololink が動く専用 venv の
   ワーカープロセスがフレームを /dev/shm の seqlock バッファへ書き、LeRobot 側は
   それを mmap で読む(LeRobot 環境の Python/numpy に依存しない)。
2. **カメラプラグイン規約**: 配布パッケージ名は `lerobot_camera_` プレフィックス必須
   (発見はエントリポイントではなく**配布名プレフィックススキャン** —
   `register_third_party_plugins()` が配布名をそのまま import する。配布名 =
   トップレベルパッケージ名、アンダースコア維持)。`XxxConfig`→`Xxx` の命名、
   `__init__.py` で両クラス re-export、`@CameraConfig.register_subclass("type名")`。
3. **Camera 抽象クラス (LeRobot 0.6.0)**: `is_connected`(property) /
   `find_cameras()`(static) / `connect(warmup=True)` / `read()` /
   `async_read(timeout_ms)` / `disconnect()` が抽象。`read_latest(max_age_ms)` は
   任意だが未オーバーライドだと FutureWarning(将来必須)。
   `Camera.__init__` が config から `self.fps/width/height` を設定する。
4. **`hololink.reset()` は発行しない**(デフォルト): リセットはカメラ側 PHY を
   再起動させ 10G リンクを落とす。リンク訓練が不安定な構成では復帰しない。
   ワーカーは reset なしで enumeration → センサー設定 → ストリーミングに直行する。
5. **10G リンクの物理運用が最大の敵**: カメラは発熱でリンクを落とす(要冷却)。
   リンク断からの復帰はケーブル抜き差しではなく **mgbe インターフェースの down/up**
   で行う。詳細は `reference_devices.md`。

# ワークフロー

## Step 1: カメラ疎通確認

- リンク確認: `cat /sys/class/net/mgbe0_0/carrier` が `1`。
  `0` の場合は `sudo ip link set mgbe0_0 down && sleep 2 && sudo ip link set mgbe0_0 up`
  でリンク窓を作る(HSB カメラは mgbe 側の初期化直後にリンク訓練が成功しやすい)。
- enumeration 確認: カメラは UDP **12267** に BOOTP をブロードキャストする(非特権
  ポートなので root 不要で受信確認できる)。プラグインの `find_cameras()` も
  この仕組みを使う。
- ホスト IP: `mgbe0_0` に 192.168.0.101/24(nmcli で永続化)。カメラの IP
  (デフォルト 192.168.0.2)は**電源断で揮発**する — ワーカーが起動時に
  `hololink set-ip` を自動実行するので事前設定は不要。
- ping が通らなくても enumeration が見えていれば接続可能なことがある(逆に
  ping が通っても enumeration が来なければ FPGA バージョン不一致を疑う)。

## Step 2: hololink 実行環境の確認

- hololink venv の存在確認: `<hsb_dir>/venv/bin/python -c "import hololink"` が
  通ること(PYTHONPATH=/opt/nvidia/holoscan/python/lib、
  LD_LIBRARY_PATH=/opt/nvidia/holoscan/lib:/usr/lib/aarch64-linux-gnu/nvidia が必要)。
- 無ければ先に HSB 環境を構築する(hsb_v2.5.0 の Docker レス構築:
  venv 作成 → `pip wheel python/ --no-build-isolation`(CUDAARCHS=Thorは110)→
  wheel インストール。所要 10〜30 分)。
- **シェルのデフォルト python3 が conda 等を向いていないか必ず確認**。
  holoscan バインディングは cpython-312 固定で、違う Python では動かない。

## Step 3: コード生成

パッケージ構成(検証済みの形):

```
lerobot_camera_hsb/
├── pyproject.toml                  # name = "lerobot_camera_hsb" (発見に必須)
├── README.md
├── lerobot_camera_hsb/
│   ├── __init__.py                 # HSBCameraConfig / HSBCamera を re-export
│   ├── configuration_hsb.py        # @CameraConfig.register_subclass("hsb")
│   ├── camera_hsb.py               # Camera 実装 (ワーカー起動 + SHM 読み)
│   ├── hsb_worker.py               # hsb venv 側ワーカー (subprocess 起動専用)
│   └── monitor.py                  # 診断 CLI (lerobot-hsb-monitor)
└── tests/
    ├── fake_worker.py              # SHM プロトコル互換の合成フレーム配信
    └── test_camera.py              # ハードウェア不要の pytest
```

実装の要点(詳細・SHM プロトコル仕様は `reference.md`):

- Config は `camera_mode` から width/height/fps を導出し、明示指定との不一致は
  ValueError にする(LeRobot はリサイズしない前提で features を確定するため)。
- `connect()`: ワーカーを Popen(env は PYTHONPATH/LD_LIBRARY_PATH/PATH を明示、
  PATH には venv/bin を含める — ワーカーが `hololink` CLI を spawn するため)→
  stdout の `READY w h fps` 行をタイムアウト付きで待つ → SHM を mmap。
  **ワーカー死亡検知時は stderr 末尾を添えた ConnectionError** にする
  (リンク断が最頻の失敗要因なので、確認コマンドをエラーメッセージに含める)。
- `read()/async_read()/read_latest()`: seqlock 読み(奇数=書き込み中は再読)。
  `async_read` は「前回返した seq と異なる seq」を待つ。ワーカー死亡は
  ポーリング中に検知して RuntimeError。
- `disconnect()`: SIGTERM → 猶予 → SIGKILL、SHM ファイル削除。
- テスト容易性のため Config に `worker_script` 差し替えフィールドを持たせる。

### 実装上の制約(ハマりやすい点。必ず守ること)

- `Camera` サブクラスに `config_class` と `name = "hsb"` のクラス変数。
- 配布名 = パッケージ名。**プロジェクトの親ディレクトリを cwd にして python を
  起動すると、プロジェクトルートが名前空間パッケージとして実パッケージを隠す**
  (editable install で "unknown location" ImportError)。テストや検証は
  プロジェクトディレクトリ内かニュートラルな cwd で行う。
- ワーカーには LeRobot 側の import を一切書かない(hololink 側 venv には
  lerobot が無い)。逆にプラグイン本体に hololink の import を書かない。
- SHM はファイルバック mmap(/dev/shm 直下)を使う。
  `multiprocessing.shared_memory` は別プロセス attach 時の resource_tracker が
  勝手に unlink する既知問題があるため使わない。
- `pyproject.toml` の `build-backend` は `setuptools.build_meta`。

## Step 4: ハードウェア不要テスト

- `fake_worker.py`(SHM プロトコル互換・合成フレーム)で pytest を実行:
  登録(`CameraConfig.get_choice_class("hsb")`)、
  `make_cameras_from_configs` からのクラス解決、connect/read/async_read/
  read_latest/disconnect、BGR 変換、タイムアウト、二重接続、切断後 read、
  ワーカー失敗時のエラーメッセージ、SHM 後始末、再接続 — を網羅する。
- fake_worker は実ワーカーと同じ CLI 引数を受け、`--fail` で接続失敗も模擬する。

## Step 5: インストールと実機確認

### インストール(確定コマンド)

```bash
cd lerobot_camera_hsb
pip install -e . --no-deps
```

※ **`--no-deps` 必須(Jetson)**: pip の依存解決が numpy 等をダウングレードし
CUDA ビルドの torch スタックを壊すことがある。

### 実機確認(確定コマンド)

```bash
# カメラ無応答時はリンク窓を作ってから
sudo ip link set mgbe0_0 down && sleep 2 && sudo ip link set mgbe0_0 up && sleep 3
lerobot-hsb-monitor --camera-mode 0 --duration 10
```

- 期待値: enumeration でカメラ検出(MAC 先頭 `8c:1f:64` = Leopard)→
  30fps 前後で安定(実測 30.2fps @2560×1984)。
- fps が出ない/途中で TimeoutError → リンク断。カメラの冷却を確認して
  mgbe down/up からやり直す。

## Step 6: lerobot-record 統合

```bash
lerobot-record \
    --robot.type=so101_follower \
    --robot.port=<ポート> \
    --robot.cameras='{"front": {"type": "hsb", "camera_mode": 1}}' \
    ...
```

- 収録では `camera_mode: 1`(1920×1080@30)が扱いやすい(フル解像度 2560×1984 は
  1フレーム 15MB。データセットサイズとエンコード負荷に注意)。
- 縮小が必要なら LeRobot 側の image transform を使う(プラグインはリサイズしない)。
- 長時間収録では**カメラの冷却を維持**すること(リンク断 = エピソード失敗)。

## Step 7: 知見の記録

- 新たな知見(未対応カメラの追加、リンク運用の改善など)は
  `./reference/reference.md` / `./reference/reference_devices.md` に追記する。
- プラグインの README.md を source of truth として扱い、コードと docs を一致させる。
- 診断 CLI(monitor)を必ず同梱する。カメラ側の物理問題(リンク/熱)と
  プラグインの問題の切り分けが大幅に速くなる。
