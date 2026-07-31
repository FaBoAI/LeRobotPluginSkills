---
name: midi-teleop-skills
description: USB接続したMIDIコントローラ(SMC-Mixer等)でロボットアームSO-101を操作するLeRobot 0.6.x テレオペレータープラグインのコード生成・動作確認を実施するスキル。フェーダーによる絶対位置制御。Jetsonでの実機構築で検証済み。
---

# 概要

JetsonにUSB接続したMIDIコントローラ(ミキサー型)で、ロボットアームSO-101(フォロワー)を
操作するLeRobotサードパーティプラグインを生成し、動作確認まで実施する。
本スキルは SMC-Mixer + SO-101 + LeRobot 0.6.0 の実機構築(2026-07)で検証済み。

ジョイスティック(joystick-teleop-skills)との根本的な違い:
**フェーダーは関節の絶対位置を指令する**(スティックの速度積分ではなく、
フェーダーの物理位置=関節角度)。このため「ソフトテイクオーバー」の実装が必須になる。

# 実装前に必ず参照する

- 実装知見(MIDI解析・絶対位置制御・落とし穴): `./reference/reference.md`
- 対応デバイスの実測プロトコル: `./reference/reference_devices.md`
- LeRobotプラグイン規約・座標系・キャリブレーションの共通知見:
  `../joystick-teleop-skills/reference/reference.md`(§1〜§5、§7.5、§8は共通)

# 前提知識(コード生成前に必ず理解すること)

1. **絶対位置制御とソフトテイクオーバー**: フェーダー位置を直接関節角度にマップすると、
   接続時・初回タッチ時にアームが飛ぶ。**ピックアップ方式**(フェーダーが関節の現在位置を
   横切るまで無効、横切ったら追従開始)を必ず実装する。
2. **MIDIバックエンドはALSA raw MIDI直読み**(`/dev/snd/midiC*D*`)。mido/python-rtmidi等の
   追加pip依存は不要(Jetsonでは依存追加が事故のもと)。MIDIパースはランニングステータス
   対応で約40行。
3. **デバイスによりポート(サブデバイス)が分かれる**: SMC-Mixerは Private/Master の
   2ポート構成で、**全データを搬送するのはMaster(サブデバイス1)**(Privateはフェーダーの
   ミラーのみでボタンが流れない)。サブデバイス選択は `/dev/snd/controlC<card>` への
   ioctl(`SNDRV_CTL_IOCTL_RAWMIDI_PREFER_SUBDEVICE` = `_IOW('U',0x42,int)`)で可能
   (alsa-libと同じ機構、依存なし)。**開いたポートが意図どおりか必ず実行時検証する**
   (詳細と1ニブル違いのPCM用ioctlの罠: reference.md §2)。
4. **座標系・リミット・プラグイン規約はジョイスティック編と共通**:
   `use_degrees=True` がデフォルト、リミットはテレオペ側の責務(キャリブレーションJSONから
   導出し、フェーダーのフルストローク=関節の全可動域にマップ)、パッケージ名は
   `lerobot_teleoperator_` プレフィックス。

# ワークフロー

## Step 1: MIDIコントローラ疎通確認

- `amidi -l` でALSA raw MIDIポートを確認(例: `hw:3,0,0 SINCO SMC-Mixer-Private` /
  `hw:3,0,1 SINCO SMC-Mixer-Master`)。
- `/dev/snd` の権限は `audio` グループ(`sudo usermod -aG audio $USER` + 再ログイン)。
- 未対応デバイスの場合は Step 2 のキャプチャでプロトコルを実測してプロファイルを追加する。

## Step 2: プロトコル実測(新デバイス対応時)

- **全ポートを同時にキャプチャ**して、どのポートに何が流れるかを確認する
  (ポートによって搬送データが異なる。SMC-MixerはPrivateがミラー+欠落、Masterが完全)。
- キャプチャの注意: `timeout N amidi -d > file` は**SIGTERMでstdioバッファが失われ
  0バイトになる**ことがある。`stdbuf -oL` を挟むか、自作リーダーで定期スナップショット
  保存にする。ユーザーへの操作指示は「フェーダー1本ずつフルストローク→ノブ→ボタン」。
- 解析観点: フェーダーは CC か Pitch Bend か(Mackie Control系は**PitchBend ch0-7**)、
  ノブは絶対値か相対値か(相対は +1=0x01/−1=0x41 等)、ボタンのNote番号と velocity。

## Step 3: ロボットアーム確認・キャリブレーション

- joystick-teleop-skills の Step 2〜3 と同一(ポート特定、テレオペ中のシリアル排他、
  WRAPPEDキャリブレーションの検出と対処)。

## Step 4: 割当・コード生成

実証済みの割当(SMC-Mixer):

| コントロール | 割当 |
|---|---|
| フェーダー1〜5 | shoulder_pan / shoulder_lift / elbow_flex / wrist_flex / wrist_roll(絶対位置)|
| フェーダー6 | gripper(下=閉 0、上=開 100)|
| フェーダー7/8・ノブ | 予備 |
| ▶(再生) | エピソード成功 |
| ■(停止) | エピソード失敗 |
| ●(録音) | 再記録 |
| ◀◀(押しっぱなし) | 人間介入フラグ |

### 実装上の制約(ハマりやすい点)

- **ソフトテイクオーバー**: 未エンゲージのフェーダーは (1) 現在目標との差が
  ε(既定3/127)以内、または (2) 前回値→今回値が目標を**横切った**とき にエンゲージ。
  横切り判定は符号テスト `(prev−equiv)×(now−equiv) ≤ 0`(高速スイープの飛び越え対策)。
- **rawMIDIは現在値を照会できない**: 接続時点のフェーダー物理位置は不明。
  「一度も動いていないフェーダーは None」を明示的に扱い、目標は initial_position を保持。
- MIDIパースは**ランニングステータス**と**リアルタイムバイト(0xF8以降)の割り込み**に
  対応する(安価なコントローラは常用する)。Note On velocity 0 = Note Off。
- アクションキーは so_leader のモーター順で毎回全キー返却、connect/disconnect ガード、
  reader は start() 成功後に代入 — ジョイスティック編の制約がすべて適用される。
- `robot_gains`(サーボPゲイン上書き)も同様に実装する(reference.md §7.5)。

## Step 5: インストールとテレオペ起動

```bash
cd lerobot_teleoperator_midi
pip install -e . --no-deps
```

```bash
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=<フォロワーのポート> \
    --robot.id=<キャリブレーション済みid> \
    --robot.max_relative_target=5 \
    --teleop.type=midi \
    --teleop.id=default \
    --teleop.robot_calibration_file=$HOME/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json \
    --teleop.robot_gains='{"p_coefficient": 32}'
```

- 起動直後はアームが initial_position(キャリブレーション中央)を保持。
  **各フェーダーをスイープして関節をピックアップさせる**手順をユーザーに伝える。

## Step 6: 動作確認

- ロボット接続前に `lerobot-midi-monitor` で全フェーダー・ボタンの認識を確認。
- ロボット接続後の確認観点: 各フェーダーのピックアップ動作 / フルストロークで
  関節が可動域いっぱいまで動くこと / フェーダー静止でアームも静止 / グリッパ開閉方向 /
  イベントボタン。

## Step 7: 知見の記録

- 新たな知見は `./reference/reference.md` に、新デバイスの実測プロトコルは
  `./reference/reference_devices.md` に追記する。
- プラグインの README.md を source of truth として扱い、コードと docs を一致させる。
