---
name: joystick-teleop-skills
description: USB接続したジョイスティック(Logitech F710等)でロボットアームSO-101を操作するLeRobot 0.6.x テレオペレータープラグインのコード生成・動作確認を実施するスキル。Jetsonでの実機構築で検証済みの手順と制約に基づく。
---

# 概要

JetsonにUSB(ワイヤレスドングル)で接続したジョイスティックで、ロボットアームSO-101(フォロワー)を
操作するLeRobotサードパーティプラグインを生成し、動作確認まで実施する。
本スキルはF710 + SO-101 + LeRobot 0.6.0 の実機構築(2026-07)で検証済みの知見に基づく。

# 実装前に必ず参照する

- 実装知見(座標系・キャリブレーション・落とし穴の詳細): `./reference/reference.md`
- LeRobotプラグイン規約・ソース該当箇所・デバイス情報: `./reference/reference_URL.md`

# 前提知識(コード生成前に必ず理解すること)

1. **座標系は度数**: LeRobot 0.6.0 の `so101_follower` は `use_degrees=True` がデフォルト。
   関節指令はキャリブレーション中央=0°の度数(フルターン=±180°)。正規化±100ではない。
   グリッパのみ常に 0..100(0=閉)。
2. **関節リミットはテレオペ側の責務**: 度数モードではLeRobotが指令を可動域にクランプしない。
   ロボットのキャリブレーションJSONから関節ごとのリミットを導出すること
   (`±(range_max−range_min)/2×360/4095` 度。ファイルが無ければ保守的に±100°)。
3. **巻き付き(円環)制御は実装禁止**: フルターン記録の関節を±180で巻き付かせると、
   サーボが境界ジャンプを「ほぼ1回転の誤差」と解釈して逆方向へ全速で巻き戻る(実機で確認済み)。
   全関節をリミットで**停止**させること。
4. **プラグイン規約**: パッケージ名は `lerobot_teleoperator_` プレフィックス必須、
   `XxxConfig`→`Xxx` の命名、`__init__.py` で両クラスを re-export、
   `@TeleoperatorConfig.register_subclass("type名")` で登録。

# ワークフロー

## Step 1: ジョイスティック疎通確認

- evdev で認識確認(pygame不要。ヘッドレスJetsonで動作):
  `python3 -c "import evdev; [print(p, evdev.InputDevice(p).name) for p in evdev.list_devices()]"`
- F710 は **XInputモード(前面スイッチ「X」)** で使う。usb id `046d:c21f`・名前
  "Logitech Gamepad F710"。DirectInputモード(`046d:c219`)を検出したら
  スイッチを「X」にするよう明示エラーで案内する。
- 認識されない場合: Logitechボタンで電源オン、ドングル挿し直し、
  `sudo usermod -aG input $USER` + 再ログイン(/dev/input/event* の権限)。
- ジョイスティックの軸レンジ・ボタンは機種やモードで異なるため、接続時に
  `capabilities()`/`absinfo()` で実際の値を収集して正規化する。

## Step 2: ロボットアームSO-101疎通確認

- シリアルポートの特定: USB列挙順(ACM0/ACM1)は再起動で入れ替わる。対象アームのUSBを
  抜いて `ls /dev/ttyACM*` で確定するか、`lerobot-find-port` を使う。
  リーダーとフォロワーの取り違えに注意。
- **警告: テレオペ実行中の /dev/ttyACM* に別プロセスからアクセスしないこと**。
  診断読み取りでもバスが衝突しセッションが `SerialException (multiple access on port?)` で落ちる。

## Step 3: キャリブレーション確認・実施

- フォロワーのキャリブレーションJSON:
  `~/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json`
- **健全性チェック**: `wrist_roll` 以外の関節で `range_min: 0, range_max: 4095` は壊れた記録
  (WRAPPED)。ホーミング姿勢がずれてスイープ中にrawカウンタが0/4095境界をまたいだ証拠。
  wrist_roll の 0..4095 のみ仕様(フルターン関節)。
- 再キャリブレーション時の注意:
  1. 既存ファイルがあるとプロンプトが出る。**ENTERだけ押すと再記録されない**。`c` + ENTER。
  2. ホーミングの「middle of range で ENTER」は**全関節を可動域の中央にした姿勢**
     (アームを立てた姿勢)で行う。休息姿勢のままだと WRAPPED が再発する。
  3. レンジ記録中のライブ表で MIN≈0 かつ MAX≈4095 になった関節があればラップ確定。やり直す。
- 再キャリブレーションは座標系を変えるため、**旧キャリブレーションで記録した
  データセット・学習済みモデルと非互換になる**ことをユーザーに伝える。
- WRAPPED のままでも運用は可能: プラグイン側でフルターン記録の関節を±180°で
  クランプすれば全域に近い操作ができる(reference.md 参照)。

## Step 4: キー割当・コード生成

実証済みのキー割当(F710 XInputモード):

| 入力 | 割当 |
|------|------|
| 左スティック 左/右 (ABS_X)  | shoulder_pan |
| 左スティック 上/下 (ABS_Y)  | shoulder_lift(上=正、軸は上下反転して扱う)|
| 右スティック 上/下 (ABS_RY) | elbow_flex(上=正)|
| 十字キー 上/下 (ABS_HAT0Y)  | wrist_flex(上=正)|
| 右スティック 左/右 (ABS_RX) | wrist_roll |
| LT (ABS_Z)                  | グリッパを開く |
| RT (ABS_RZ)                 | グリッパを閉じる |
| Y / A / X ボタン            | エピソード成功 / 失敗 / 再記録(get_teleop_events)|
| RB(押しっぱなし)           | 人間介入フラグ |
| Ctrl+C                      | 終了(lerobot-teleoperate が KeyboardInterrupt を処理)|

制御方式: スティック入力を速度として関節目標位置に**積分**する(長押しで連続動作)。

### 実装上の制約(ハマりやすい点。必ず守ること)

- `Teleoperator` サブクラスに `config_class` と `name = "<type名>"` のクラス変数が必須。
- アクションは `<motor>.pos` キーの辞書で、**順序を so_leader のモーター順に揃える**:
  `shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper`。
  **毎回すべてのキーを返す**(欠けると lerobot-record が KeyError、空辞書 `{}` は禁止)。
- `connect(calibrate: bool = True)` シグネチャ +
  `@check_if_already_connected` / disconnect に `@check_if_not_connected`(so_leader互換)。
- **リーダー(reader)オブジェクトは start() 成功後に self へ代入**。失敗時に半接続状態が
  残るとリトライが DeviceAlreadyConnectedError になる。
- 積分の dt に上限(例 0.1s)を設ける。ループが止まっても目標がジャンプしない。
- 無線切断時は最終指令位置を保持(ゼロ指令や例外での急停止より安全)。
- デッドゾーンは再スケール式 `sign×(|v|−dz)/(1−dz)`。
- 目標値のクランプは**毎回の get_action で実施**(初回 dt=0 でも範囲外の初期姿勢を正規化)。
- 速度はグローバル倍率(`speed_scale`)+関節ごとの速度辞書で調整可能にする。
  **elbow_flex は同一指令速度でも体感が速い**ため、デフォルトを他の70%程度に下げる。
- 起動直後に HOME 姿勢へ飛ばさない工夫として `initial_position` を設定可能にし、
  `--robot.max_relative_target=5` の併用を案内する(毎ティック present±5 にクリップ)。
- `pyproject.toml` の `build-backend` は `setuptools.build_meta`。

## Step 5: インストールとテレオペ起動

### インストール(確定コマンド)

```bash
cd lerobot_teleoperator_<type名>
pip install -e . --no-deps
```

※ **`--no-deps` 必須(Jetson)**: pipの依存解決が numpy 等をダウングレードし
CUDAビルドの torch スタックを壊すことがある(実際に発生)。

### 起動(確定コマンド)

```bash
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=<フォロワーのポート> \
    --robot.id=<キャリブレーション済みid> \
    --robot.max_relative_target=5 \
    --teleop.type=<type名> \
    --teleop.id=default \
    --teleop.robot_calibration_file=$HOME/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json
```

- `robot_calibration_file` で関節ごとのリミットを自動導出(度数モードでの安全確保に必須)。
- 起動が確認できたら「動作確認してください」とユーザーに明示する。

## Step 6: 動作確認

- 最初は低速(`--teleop.speed_scale=0.5` 程度)で全関節の方向・可動範囲を確認。
- 確認観点: 各関節が端(リミット)で**止まる**こと(反対側へ巻き戻らないこと)/
  スティックを離すとその場で保持すること / グリッパの開閉方向 / 体感速度のバランス。
- 速度調整: `--teleop.speed_scale=` で全体、`--teleop.joint_speeds='{"elbow_flex": 50.0}'` で個別。
- 終了は Ctrl+C。

## Step 7: 知見の記録

- 今回の実装で得た新たな知見は `./reference/reference.md` に追記する。
- プラグインの README.md を source of truth として扱い、コードと docs を常に一致させる。
- 可能ならプラグインに検証CLIを同梱する(入力モニタ、キャリブレーション健全性チェック)。
  ロボット接続前の切り分けが大幅に速くなる。
