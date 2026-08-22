# lerobot_teleoperator_otter_leader

LeRobot 用 Otter Leader テレオペレータプラグインです。

**LeRobot 0.6.x 対応済み** (2026-08-12 に 0.6.0 実環境で結合検証済み:
プラグイン自動発見 / config 解決 / インスタンス生成 / モータIDマップ)。

## インストール (LeRobot 0.6.x)

```bash
cd otter-leader
pip install -e . --no-deps
pip install dynamixel-sdk deepdiff   # lerobot のオプション依存 (未導入の場合)
```

- **`--no-deps` 必須 (Jetson)**: pip の依存解決が numpy 等をダウングレードし
  CUDA ビルドの torch スタックを壊すことがあるため。
- LeRobot 0.6.x は Python >= 3.12 が必要。
- インストール後は `--teleop.type=otter_leader` がそのまま使える
  (`lerobot-teleoperate` / `lerobot-record` が起動時にプラグインを自動発見する。
  `--plugins` フラグ等は不要)。

```bash
lerobot-teleoperate \
    --teleop.type=otter_leader \
    --teleop.port=/dev/ttyUSB0 \
    --teleop.id=my_otter \
    --robot.type=<フォロワー> ...
```

## 変更点

- Leader を **7DOF + Gripper** の 8 軸構成に変更
- デフォルトの ID 対応を以下の実機 Leader 配線に修正
  - LEFT: `1, 2, 3, 4, 5, 6, 7, 8`
  - RIGHT: `11, 12, 13, 14, 15, 16, 17, 18`
- LEFT / RIGHT それぞれの開始 ID、サーボ数、ID 順、個別 ID リストを設定ファイルから変更可能
- モータ名も必要に応じて設定ファイルから変更可能
- `setup_motors()` は ID の小さい順に実行

## デフォルト ID 対応

```text
LEFT
ID 1: left_shoulder_yaw
ID 2: left_shoulder_pitch
ID 3: left_shoulder_roll
ID 4: left_elbow_pitch
ID 5: left_wrist_yaw
ID 6: left_wrist_pitch
ID 7: left_wrist_roll
ID 8: left_gripper

RIGHT
ID 11: right_shoulder_yaw
ID 12: right_shoulder_pitch
ID 13: right_shoulder_roll
ID 14: right_elbow_pitch
ID 15: right_wrist_yaw
ID 16: right_wrist_pitch
ID 17: right_wrist_roll
ID 18: right_gripper
```

## 設定例

既存の LeRobot 設定ファイルの `otter_leader` 設定部分に、必要に応じて以下のキーを追加してください。

```yaml
type: otter_leader
port: /dev/ttyUSB0

use_left: true
use_right: true

# LEFT Leader: 最小 ID 1 から 8 個を昇順に割り当てる
left_start_id: 1
left_motor_count: 8
left_id_order: ascending

# RIGHT Leader: 最小 ID 11 から 8 個を昇順に割り当てる
right_start_id: 11
right_motor_count: 8
right_id_order: ascending
```

ID が連番ではない場合、または ID の順番を完全に固定したい場合は、`*_motor_ids` を指定すると `*_start_id` / `*_motor_count` / `*_id_order` より優先されます。

```yaml
left_motor_ids: [1, 2, 3, 4, 5, 6, 7, 8]
right_motor_ids: [11, 12, 13, 14, 15, 16, 17, 18]
```

デフォルトの 8 軸モータ名は、7DOF + Gripper に合わせて以下にしています。
`motor_names` の順番と `*_motor_ids` の順番が対応します。

```yaml
motor_names:
  - shoulder_yaw
  - shoulder_pitch
  - shoulder_roll
  - elbow_pitch
  - wrist_yaw
  - wrist_pitch
  - wrist_roll
  - gripper
```

LEFT / RIGHT で別名にしたい場合は、以下のように指定できます。

```yaml
left_motor_names:
  - shoulder_yaw
  - shoulder_pitch
  - shoulder_roll
  - elbow_pitch
  - wrist_yaw
  - wrist_pitch
  - wrist_roll
  - gripper

right_motor_names:
  - shoulder_yaw
  - shoulder_pitch
  - shoulder_roll
  - elbow_pitch
  - wrist_yaw
  - wrist_pitch
  - wrist_roll
  - gripper
```

`gripper_motor_names` に含まれる名前は `RANGE_0_100` / `CURRENT_POSITION` として扱います。その他の関節は `RANGE_M100_100` として読み、`get_action()` では `0〜100` に変換します。

```yaml
gripper_motor_names: [gripper]
inverted_motor_names: [elbow_pitch]
gripper_open_pos: 50.0
```
