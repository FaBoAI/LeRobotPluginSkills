# lerobot_camera_hsb

Jetson AGX Thor 上の **Holoscan Sensor Bridge (Leopard VB1940 Eagle 10GigE PoE)** カメラを
**LeRobot 0.6.x** から使うためのサードパーティカメラプラグイン。

[FaBoAI/LeRobotPluginSkills](https://github.com/FaBoAI/LeRobotPluginSkills) の
プラグイン規約 (配布名プレフィックス発見 / `XxxConfig`→`Xxx` 命名 / `__init__` re-export) に準拠。

## アーキテクチャ

hololink/holoscan のネイティブスタックは Python 3.12 + numpy 1.26 に固定されているため、
LeRobot 側の環境と衝突しないよう**プロセス分離**する:

```text
[LeRobot プロセス]                         [hsb venv (Python 3.12) ワーカー]
HSBCamera.connect() ──── subprocess ────▶ hsb_worker.py
     ▲                                       │  hololink → holoscan パイプライン
     │                                       │  (LinuxReceiver→CSI→ISP→demosaic→gamma)
     └──── /dev/shm seqlock バッファ ◀────── │  RGB8 フレームを書き続ける
```

- ワーカーは `/home/jetson/camera/venv`(`install_hsb.py` が構築)で動く
- フレームは `/dev/shm` の seqlock 付き共有バッファ経由(コピー1回、ロックフリー)
- LeRobot 側は Python/numpy のバージョンに依存しない

## 前提

1. HSB 環境構築済み: `/home/jetson/camera/install_hsb.py` 実行済み(venv + hololink wheel)
2. カメラのネットワーク設定済み(mgbe0_0 = 192.168.0.101/24)
3. **カメラの冷却**(重要): VB1940 は発熱で 10G リンクを落とす。運用時はファン必須
4. LeRobot 0.6.x(Python ≥3.12)

## インストール

```bash
cd lerobot_camera_hsb
pip install -e . --no-deps    # Jetson では --no-deps 必須 (CUDA torch/numpy スタック保護)
```

インストールするだけで `lerobot-record` / `lerobot-teleoperate` 起動時に
`register_third_party_plugins()` が自動発見する(`--plugins` フラグ等は不要)。

## 使い方

```bash
lerobot-record \
    --robot.type=so101_follower \
    --robot.port=/dev/ttyACM0 \
    --robot.cameras='{"front": {"type": "hsb", "camera_mode": 1}}' \
    ...
```

Python から直接:

```python
from lerobot_camera_hsb import HSBCamera, HSBCameraConfig

cam = HSBCamera(HSBCameraConfig(camera_mode=1))   # 1920x1080@30
cam.connect()
frame = cam.async_read()      # (1080, 1920, 3) uint8 RGB
cam.disconnect()
```

## 設定 (HSBCameraConfig)

| フィールド | 既定値 | 説明 |
|---|---|---|
| `camera_mode` | `0` | 0: 2560×1984@30 / 1: 1920×1080@30 / 2: 2560×1984@60 / 3: 2560×1984@30(RAW8) |
| `hololink_ip` | `192.168.0.2` | カメラ (HSB) の IP |
| `color_mode` | `rgb` | `rgb` / `bgr` |
| `fps` `width` `height` | mode から自動 | 明示指定時は mode と一致必須 |
| `hsb_python` | `/home/jetson/camera/venv/bin/python` | hololink が入った venv の python |
| `connect_timeout_s` | `90` | enumeration + センサー設定 + 初フレームまでの許容時間 |
| `reset` | `False` | `hololink.reset()` を発行(リンク不安定環境では False 推奨) |
| `warmup_s` | `1.0` | connect 後のウォームアップ |

## 診断 CLI

```bash
lerobot-hsb-monitor --camera-mode 1 --duration 10
```

リンク状態 → enumeration → 接続 → fps 計測、を一括チェックする。

## テスト (ハードウェア不要)

```bash
python -m pytest tests/ -v    # fake_worker が実カメラを模擬
```

## トラブルシュート

接続失敗時のエラーメッセージに確認手順が出る。詳細は
`/home/jetson/camera/SKILL.md` の「既知の問題と対処」参照。要点:

- `carrier` が 0 → リンク断。`sudo ip link set mgbe0_0 down && sleep 2 && sudo ip link set mgbe0_0 up` で窓を作る
- リンクがすぐ落ちる → **カメラを冷却する**(最重要)
- `Device with 192.168.0.2 not found` → リンク層の問題(ソフトではない)
