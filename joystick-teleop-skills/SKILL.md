---
name: joystick-teleop-skills
description: USB接続しているジョイスティックを操作してロボットアームSO-101を操作できるコード生成・動作確認を実施するスキル
---

#概要
JetsonとUSB(BLEドングル経由)を接続し、認識できているジョイスティックの操作でロボットアームSO-101を操作するLeRobotプラグインコードを生成し、動作確認を実施する

#　実装前に以下を必ず参照して設計する
LeRobotサードパーティプラグインに関する情報
　./reference/reference_URL.md
ジョイスティック SO-101テレオペレーター実装上の知見
　./reference/reference.md

#ワークフロー
## Step 1
ジョイスティック疎通確認
- ジョイスティックがJetson側から認識できているか確認する
- 認識されていない場合は、警告を表示してトラブルシューティングに誘導する
- ジョイスティックは固定ではないため、都度キー情報を収集する

## Step 2
ロボットアームSO-101疎通確認
- ロボットアームがJetson側から認識できているか確認する
- 認識されていない場合は、警告を表示してトラブルシューティングに誘導する

## Step 3
キャリブレーションファイル確認・キャリブレーション実施
- ロボットアームのキャリブレーションが必要な場合は、その操作手順をユーザーに促す
- キャリブレーションファイルの存在が確認できた場合は、キャリブレーションファイルあり、として次のステップに進む

## Step 4
キー割当・コード生成
- ジョイスティックの十字キー、ボタンをロボットアームの関節操作に割り当てる

- 以下のキー割当を**必ず**使用すること（変更禁止）
| 入力 | 関節 |
|------|------|
| 左スティック 左/右 (ABS_X)   | shoulder_pan  |
| 左スティック 上/下 (ABS_Y)   | shoulder_lift（上下反転）|
| 右スティック 左/右 (ABS_Z)   | elbow_flex    |
| 右スティック 上/下 (ABS_RZ)  | wrist_flex    |
| 十字キー 左/右 (ABS_HAT0X)   | wrist_roll    |
| 十字キー 上/下 (ABS_HAT0Y)   | gripper（上下反転）|
| SELECT (BTN_SELECT=314)      | 緊急停止（即時終了） |
| START (BTN_START=315)        | 通常終了      |


- キー操作は長押しを受けつけ、ロボットアームと連動して連続動作する
- 緊急停止ボタン、終了ボタンの定義もする

### 実装上の制約
以下はハマりやすい点のため、コード生成時に必ず守ること。

- `Teleoperator` サブクラスには `name = "teleop_type"` クラス変数が必須
  （定義しないと `'XXX' object has no attribute 'name'` エラー）
- `get_action()` から空辞書 `{}` を返してはならない
  （`StopIteration` クラッシュの原因になる）
- ループ終了には `KeyboardInterrupt` を送出する
  （`SystemExit` はループが止まらない場合がある）
- `pyproject.toml` の `build-backend` は `setuptools.build_meta` を使う
  （`setuptools.backends.legacy:build` は古い setuptools で利用不可）

### デバイス自動検出（環境非依存化）
- `config_joystick_so101.py` の `device_path` デフォルト値は `""` にする
- `connect()` の冒頭で `device_path` が空文字の場合、以下のロジックで自動検出する
  - `evdev.list_devices()` を走査し、`EV_ABS` ケイパビリティに `ABS_X` と `ABS_Y` を
    両方持つ最初のデバイスをジョイスティックとして使用する
  - 特定メーカー・機種への依存は持たない
- 見つからない場合は `RuntimeError` で明示的にエラーを出し、ユーザーに接続確認を促す
- `device_path` が明示指定されている場合はそちらを優先する（固定運用も可能）

## Step 5
テレオペレーション起動
- テレオペレーションを実行できる状態になったら、以下の手順をユーザーに伝達する

### インストール（確定コマンド）
```bash
cd lerobot_teleoperator_joystick_so101   # lerobot_teleoperator_{teleop.type} の命名規則に従う
pip install -e . --no-deps --config-settings editable_mode=compat
```
※ `editable_mode=compat` が必須。省略すると LeRobot にプラグインが検出されない。

### 起動（確定コマンド）
```bash
conda activate lerobot && lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=/dev/ttyACM0 \
    --robot.id=default \
    --teleop.type=joystick_so101 \
    --teleop.id=default
```

- LeRobotCLIでの起動直後に HOME pose（全関節 0.0）を送信してはならない
- テレオペレーションの起動が確認できたら、動作確認してください　とユーザーに明示する
- ユーザー操作直後、HOME pose（全関節 0.0）が参照されるのは現時点でやむを得ない

## Step 6
動作確認と動作確認終了
- 緊急停止ボタン、または終了ボタンが押下されたら動作確認を終了する

## Step 7
動作確認終了後に以下を実施する

今回の実装で得た新たな知見は ./reference/reference.md に記録すること。
README.md を source of truth として扱うこと
コードと docs を一致させること



