---
name: otter-rs-teleop-skills
description: 両腕ヒューマノイド(Dynamixel XL330 リーダー「OtterLeader」+ RobStride CAN フォロワー「RSFollower」、7DOF+グリッパ×左右=16軸ペア)を LeRobot 0.6.x のテレオペレータ/ロボットプラグインとして開発・運用するスキル。リーダーの重力ドリフト対策(弱電流保持)、フォロワーの初期位置ランプ、グリッパ過電流ガードなど、実機収録運用(2026-08)で検証済みの知見に基づく。
---

# 概要

FaBo 製の両腕ヒューマノイド構成を LeRobot で動かす 2 つのサードパーティプラグインを
開発・検証・運用するスキル。

- **OtterLeader** (`--teleop.type=otter_leader`): Dynamixel XL330 ×16 のリーダーアーム。
  USB シリアル (FTDI, `/dev/ttyUSB0`)。読み取り専用だが、グリッパと肩3軸だけは
  弱トルクの位置制御を使う(後述のドリフト対策)。
- **RSFollower** (`--robot.type=rs_follower`): RobStride RS00/03/05/06 ×16 の
  フォロワーアーム。SocketCAN (`can0`, bitrate 1M)、MIT 方式の位置制御 (kp=20, kd=1, 50Hz)。

action key は両者とも `<side>_<joint>.pos` の 16 キーで完全に対になる
(`left_shoulder_yaw.pos` … `right_gripper.pos`)。値は teleop 単位 0〜100。

# 実装前に必ず参照する

- 実装知見(ドリフト対策・初期位置運用・USB 瞬断・プラグイン規約・収録運用):
  `./reference/reference.md`
- **実機検証済みの完全な実装(リファレンス)**:
  `./plugin/lerobot_teleoperator_otter_leader/` と `./plugin/lerobot_robot_rs_follower/`
  — そのまま `pip install -e . --no-deps` で使用可能。新環境ではまずこれを試し、
  差分が必要な場合のみコード生成する。

# 前提知識(コード生成前に必ず理解すること)

1. **0.6.0 プラグイン発見は配布名プレフィックススキャン**:
   `register_third_party_plugins()` が配布名 `lerobot_teleoperator_` / `lerobot_robot_`
   で始まるパッケージを配布名のまま import する。配布名 = トップレベルパッケージ名。
   `XxxConfig`→`Xxx` の命名解決、`__init__.py` での re-export、
   `@TeleoperatorConfig.register_subclass("otter_leader")` /
   `@RobotConfig.register_subclass("rs_follower")`。詳細は `reference.md` §4。
2. **ID マップは左右×リーダー/フォロワーで向きが違う**: リーダーは
   shoulder_yaw→gripper の昇順 (L:1-8 / R:11-18)、フォロワーは
   gripper→shoulder_yaw の昇順 (L:0x01-0x08 / R:0x11-0x18)。
   対応表は `reference.md` §4 の表が正。
3. **リーダーの肩3軸は素通しではない**: XL330 の Operating Mode 5
   (電流制限付き位置制御) + Goal_Current 25mA の弱バネで接続時姿勢に保持している
   (重力ドリフト対策、`reference.md` §1)。読み値は通常どおり使える。
4. **フォロワーは connect()/disconnect() が「動く」**: `initial_position` 設定時、
   connect() は初期姿勢へのブロッキングランプ移動(最大 0.5rad/s)を完了してから返り、
   disconnect() もトルク切断前に同じランプで初期姿勢へ復帰する (`reference.md` §2)。
   周囲の安全を確認してから接続・切断すること。
5. **インストールは `pip install -e . --no-deps`** (Jetson): pip の依存解決が
   numpy 等をダウングレードし CUDA ビルドの torch スタックを壊すため。
   追加依存は手動で: リーダー = `dynamixel-sdk deepdiff`、フォロワー = `python-can PyYAML`。

# ワークフロー

## Step 1: プラグイン規約確認とインストール

```bash
cd plugin/lerobot_teleoperator_otter_leader && pip install -e . --no-deps
cd ../lerobot_robot_rs_follower && pip install -e . --no-deps
pip install dynamixel-sdk deepdiff python-can PyYAML   # 未導入の場合のみ
```

- `pyproject.toml` の `name` がパッケージディレクトリ名と一致していることを確認
  (`lerobot_teleoperator_otter_leader` / `lerobot_robot_rs_follower`)。
- プロジェクトの親ディレクトリを cwd にして python を起動しない
  (名前空間パッケージが editable install を隠す既知の罠)。

## Step 2: 結合検証(ハードウェア不要)

実機接続前に、発見→config→クラス解決→ID マップを一括で確認する:

```python
from lerobot.utils.import_utils import register_third_party_plugins
register_third_party_plugins()

from lerobot.teleoperators.config import TeleoperatorConfig
from lerobot.robots.config import RobotConfig
assert TeleoperatorConfig.get_choice_class("otter_leader").__name__ == "OtterLeaderConfig"
assert RobotConfig.get_choice_class("rs_follower").__name__ == "RSFollowerConfig"

from lerobot_teleoperator_otter_leader import OtterLeader, OtterLeaderConfig
from lerobot_robot_rs_follower import RSFollower, RSFollowerConfig
leader = OtterLeader(OtterLeaderConfig(port="/dev/ttyUSB0", id="blue"))
follower = RSFollower(RSFollowerConfig(channel="can0", id="black"))

# 16 キーが対になっていること (テレオペの前提)
assert set(leader.action_features) == set(follower.action_features)
assert len(leader.action_features) == 16
```

- RSFollower はインスタンス化時に同梱 YAML
  (`configs/rs_follower_7dof_gripper.yaml`) を読み込む。ログの
  `loaded YAML config from …; applied keys=…` で適用キーを確認。
- CAN ID の重複(例: right_shoulder_yaw を 0x08 にする誤設定)は
  ここで ValueError になる。

## Step 3: ハードウェア準備

```bash
# CAN (フォロワー)
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 restart-ms 100
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up

# リーダーのポートは決め打ちしない (USB 瞬断で番号が変わる)
LEADER_PORT=$(ls /dev/ttyUSB* 2>/dev/null | head -1)
```

- 事前チェック: `cat /sys/class/net/can0/operstate` が `up`、`/dev/ttyUSB*` が存在。
- フォロワーの現在角度読み取りは**プラグイン同梱の `scripts/get_angle.py`** を使う
  (`get_angle_script` 未指定時の既定。見つからない場合のみ旧来の `~/RS/get_angle.py` に
  フォールバック)。`initial_position_require_feedback: true` のとき、読めないと
  安全のため起動を止める。

## Step 4: キャリブレーション

- **0.4.0 時代のキャリブレーション JSON はそのまま 0.6.0 で読める**(再キャリブ不要。
  実機で検証済み)。場所は `~/.cache/huggingface/lerobot/calibration/` 配下:
  - リーダー: `teleoperators/otter_leader/<id>.json`(16 モータ、lerobot 標準形式)
  - フォロワー: `robots/rs_follower/<id>.json`(独自形式 version 20、
    open_rad/close_rad のレンジ辞書。旧 6DOF キーからの自動移行付き)
- 新規キャリブレーション:
  - リーダー: `calibrate()` — 全関節を中央にして half-turn homing → 全関節を
    可動域全体に動かして range 記録(ENTER で確定)。
  - フォロワー: `calibrate()` — 関節ごとに手で全域を動かし Ctrl+C で確定
    (get_angle.py で実角度を読む)。`inverted_motor_names` の軸は open/close が反転して
    保存される(既定 YAML: right_shoulder_yaw/pitch/roll, right_wrist_pitch, right_gripper)。
- **警告: フォロワーの再較正 = 座標系の再定義**。正規化 0-100 の物理的意味が変わり、
  旧較正で収録したデータセット・学習済みモデルと非互換になる(YAML の
  `initial_position` が指す物理姿勢も変わる)。収録シリーズの途中で再較正しない。
  詳細と座標系ずれの判定方法は `reference.md` §8。

## Step 5: teleop 起動

```bash
lerobot-teleoperate \
    --robot.type=rs_follower --robot.id=black --robot.channel=can0 \
    --teleop.type=otter_leader --teleop.id=blue --teleop.port=$LEADER_PORT \
    --fps=60
```

起動時に確認すること:

- リーダーのログに `drift-hold: … に弱保持` が **6 行**(肩3軸×左右)出ること
  (shoulder_yaw/pitch は 25mA、shoulder_roll は 18mA)。オペレーターは接続時の
  姿勢が中立として保持されるので、**リーダーを中立姿勢に構えてから接続**する。
- 操作感が重い/ドリフトが残る場合は `--teleop.drift_hold_current_ma=<20-60>` で調整
  (実機調整の経緯: 40 →「重い」→ 25 が既定、さらに shoulder_roll のみ
  `drift_hold_current_overrides` で 18 に緩和 — 13 は弱すぎで 18 に確定
  (2026-08-29 実機確認)。0 で無効)。
- フォロワーは connect() 内で `initial_position`(YAML のデータセット開始姿勢の平均)へ
  ゆっくり移動してから制御ループが始まる。**リーダーも初期位置付近に構えてから**
  ループ開始するとジャンプしない。
- 終了 (Ctrl+C) 時はフォロワーが初期位置へゆっくり戻ってからトルクが切れる。
  戻したくない場合は `--robot.return_to_initial_on_disconnect=false`。

## Step 6: 収録運用

```bash
pgrep -f "[l]erobot-(record|train|rollout|teleoperate)" && echo "二重起動!" && exit 1

lerobot-record \
    --robot.type=rs_follower --robot.id=black --robot.channel=can0 \
    --robot.cameras="{ front: {type: hsb, camera_mode: 1, exposure: 1000, analog_gain: 6}}" \
    --teleop.type=otter_leader --teleop.id=blue --teleop.port=$LEADER_PORT \
    --dataset.repo_id=local/<name> --dataset.root=<path> \
    --dataset.push_to_hub=false --dataset.single_task="..." \
    --dataset.fps=30 --dataset.episode_time_s=20 --dataset.reset_time_s=5 \
    --dataset.num_episodes=30 \
    --display_data=false \
    --play_sounds=true
```

- **毎フレームのログは DEBUG レベルに置くこと**(INFO に置いた毎フレームログは
  制御ループを 0.2Hz まで減速させた実績あり)。両プラグインとも実装済み。
- **二重起動ガード必須**: 同じバス/ポートに 2 プロセスが繋がると挙動が壊れる。
  上記の pgrep をスクリプトの事前チェックに入れる。
- ヘッドレス (SSH) では `--display_data=false` 必須(rerun のチャネル詰まりで
  ループがブロックする)。
- **エピソード間の自動初期位置復帰 + 原点保持**(2026-08-28/29):
  各エピソード後のリセット区間の頭でフォロワーが `return_to_initial_position()`
  により初期位置へゆっくり戻り(ブロッキング ~5 秒)、**リセット区間中は
  テレオペ遮断で原点を保持** — 次の「Recording episode N」アナウンスまで
  リーダーを動かしてもフォロワーは動かない。ただしこれは
  **site-packages の `lerobot_record.py` へのパッチが前提**
  (バックアップ `.bak.epreset` / `.bak.holdreset`、**venv 再構築時は要再適用**)。
  `--dataset.reset_time_s=5` 推奨 = 復帰 ~5 秒(reset_time_s の外)+
  配置 5 秒。詳細は `reference.md` §2。
- **音声ガイド `--play_sounds=true`**: `spd-say`(USB スピーカー)が
  エピソード開始/リセットを読み上げる。開始タイミング問題の解消
  (`reference.md` §5)。
- グリッパは過電流ガードが常時有効(把持トルク **3.2N・m** / limit_cur 8.0A +
  20Hz 監視。2026-08-29 に 4.0N・m 系から再調整 — 「すぐロック」の真因は
  **モータ内部の堵転保護**(≈6.8A 持続で発火)で、把持予算を保護域外
  (定常 ≈5.2A)に置く + フォルト自動復旧で解決。詳細は `reference.md` §5)。
  `HARD LIMIT: backing off` ログが出たら把持対象を確認。ガードのトリップ履歴は
  `/home/jetson/Otter/outputs/gripper_guard.log` に恒久記録される。
- USB serial error -71 で収録が中断したら、続きから `--resume=true` で再開する
  (connect の 1 回リトライで自動復帰することも多い。`reference.md` §3)。
- 状態表示ディスプレイ(OtterTools)併用時は run スクリプトの
  `tool/display_status.sh` / `tool/display_idle_daemon.py` が状態を表示する
  (未接続なら無害。`reference.md` §5)。

## Step 7: 知見の記録

- 新たな知見(電流値の再調整、新しい軸構成、ガードの閾値変更など)は
  `./reference/reference.md` に追記する。
- プラグインの README.md を source of truth として扱い、コードと docs を一致させる。
- 数値パラメータ(drift_hold_current_ma、ランプ速度、ガード閾値)を変えたら
  必ず実機の操作感で確認し、経緯をコメントに残す(既定値には調整履歴がある)。
