---
name: spacemouse-teleop-skills
description: 3Dconnexion SpaceMouse(6自由度デバイス)でロボットアームSO-101を操作するLeRobot 0.6.x テレオペレータープラグインのコード生成・動作確認を実施するスキル。6軸同時の速度制御。Jetsonでの実機構築で検証済み。
---

# 概要

3Dconnexion SpaceMouse(SpaceMouse Compact等)で、ロボットアームSO-101(フォロワー)を
操作するLeRobotサードパーティプラグインを生成し、動作確認まで実施する。
本スキルは SpaceMouse Compact + SO-101 + LeRobot 0.6.0 の実機構築(2026-07)で検証済み。

制御方式はジョイスティックと同じ**速度積分**だが、6自由度パックで**6軸を同時に**操作できる。
最重要の実装課題は**リリース検出**(下記)。

# 実装前に必ず参照する

- 実装知見(リリース検出・軸特性・落とし穴): `./reference/reference.md`
- 対応デバイスの実測データ: `./reference/reference_devices.md`
- プラグイン規約・座標系・キャリブレーション・サーボゲインの共通知見:
  `../joystick-teleop-skills/reference/reference.md`(§1〜§5、§7.5、§8は共通)

# 前提知識(コード生成前に必ず理解すること)

1. **リリース=イベント沈黙(最重要)**: SpaceMouseはEV_RELで「現在の変位」(±350)を
   操作中約60Hzでストリームするが、**カーネルのinput層は値0の相対イベントを破棄する**ため、
   手を離しても「ゼロ」は届かず単にイベントが止まる。素朴に最終値をキャッシュすると
   **手を離した瞬間の変位で関節が動き続ける(暴走)**。
   対策: **軸ごとのホールドタイムアウト**(既定0.1秒。新着イベントが無い軸は0扱い)。
2. **6軸→6関節の速度積分**: 変位を速度として目標位置に積分(ジョイスティック編と同じ
   dt上限・毎callクランプ・リミット導出)。ツイスト=旋回、押し込み=肩、が直感的。
3. **クロストークが大きい**: スライド操作にチルト成分が混入する。デッドゾーンは
   ゲームパッドより大きめ(0.1〜0.15)。
4. **権限**: /dev/input のudevルール(vendor 256f)または input グループが必要。
5. プラグイン規約・座標系(use_degrees)・リミット責務はジョイスティック編と共通。

# ワークフロー

## Step 1: SpaceMouse疎通確認

- `lsusb | grep 256f` でUSB認識確認(3Dconnexionのvendor idは`256f`。旧機種は`046d`)。
- evdevノードの確認: `/proc/bus/input/devices` で "3Dconnexion" を探し `Handlers=eventX`。
- 権限が無い場合(evdevスキャンで見えない):
  ```bash
  echo 'KERNEL=="event*", ATTRS{idVendor}=="256f", MODE="0660", GROUP="input", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/99-spacemouse.rules
  sudo udevadm control --reload && sudo usermod -aG input $USER
  ```
  (即時解放は `sudo setfacl -m u:$USER:rw /dev/input/eventX`)

## Step 2: 軸特性の実測(新デバイス対応時)

- タイムライン付きキャプチャで「1動作ずつ10秒間隔」で操作してもらい、
  物理動作→REL軸コードの対応と値レンジを確定する。
- **必ず確認する項目**: リリース時にゼロイベントが来るか(来ない前提で設計)、
  フルスケール値(Compactは±350)、操作中のストリーミングレート(約60Hz)、
  クロストークの大きさ。

## Step 3: ロボットアーム確認・キャリブレーション

- joystick-teleop-skills の Step 2〜3 と同一。

## Step 4: 割当・コード生成

実証済みの割当(SpaceMouse Compact):

| 操作(チャンネル) | REL軸 | 割当 |
|---|---|---|
| ツイスト(twist) | REL_RZ | shoulder_pan |
| 押し下げ/引き上げ(push) | REL_Z | shoulder_lift(下げ=下降になるよう速度符号を反転) |
| 前後スライド(slide_y) | REL_Y | elbow_flex |
| 前後チルト(tilt_fwd) | REL_RX | wrist_flex |
| 左右チルト(tilt_side) | REL_RY | wrist_roll |
| 左右スライド(slide_x) | REL_X | 予備 |
| 左ボタン(BTN_0)/右ボタン(BTN_1) | — | グリッパ開/閉 |

### 実装上の制約(ハマりやすい点)

- **ホールドタイムアウト必須**(前提知識1)。0.1秒推奨 — 操作中は60Hzで更新されるので
  誤失効しない。0.2秒超はリリース応答が鈍り危険側になる。
- Compactはボタン2個のみ → グリッパに使い、**エピソードイベントは実装しない**
  (`get_teleop_events` は全False固定。lerobot-record はキーボード操作で代替可能)。
- 方向の好みが分かれるため、**速度符号(joint_speeds負値)で軸ごとに反転可能**にする。
- アクションキー順・全キー毎回返却・connect/disconnectガード・reader代入順序・
  毎callクランプ・robot_gains — ジョイスティック編の制約がすべて適用される。

## Step 5: インストールとテレオペ起動

```bash
cd lerobot_teleoperator_spacemouse
pip install -e . --no-deps
```

```bash
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=<フォロワーのポート> \
    --robot.id=<キャリブレーション済みid> \
    --robot.max_relative_target=5 \
    --teleop.type=spacemouse \
    --teleop.id=default \
    --teleop.robot_calibration_file=$HOME/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json \
    --teleop.robot_gains='{"p_coefficient": 32}'
```

## Step 6: 動作確認

- ロボット接続前に `lerobot-spacemouse-monitor` で6チャンネル+ボタンを確認。
- **リリーステスト必須**: 強く変位させた状態からパッと手を離し、目標値が即座に
  停止・保持されること(ドリフト0)を確認する。
- 各軸の方向が直感に合うか確認し、逆なら `joint_speeds` の符号で反転。
- 敏感すぎる場合は `deadzone` を0.15へ、全体速度は `speed_scale` で調整。

## Step 7: 知見の記録

- 新たな知見は `./reference/reference.md` に、新デバイスの実測は
  `./reference/reference_devices.md` に追記する。
- プラグインの README.md を source of truth として扱う。
