# LeRobot 0.6.0 ジョイスティック・テレオペレータープラグイン実装知見

Logitech F710 / ELECOM JC-U3912T + SO-101 フォロワー + Jetson (JetPack/L4T) での実機構築
(2026-07-30〜31)で得た知見。
実装の実例: `lerobot_teleoperator_f710`(evdevバックエンド、機種プロファイル自動識別、
pytest 40件、実機検証済み)。機種別の軸配置・レンジ・癖は `reference_devices.md` を参照。

---

## 1. プラグイン検出の仕組みと命名規約

- パッケージ(distribution)名は `lerobot_teleoperator_` で始めること。`register_third_party_plugins()`
  (`lerobot/utils/import_utils.py`)が `importlib.metadata` で走査し、**distribution名をそのまま
  `importlib.import_module()` に渡す**ため、distribution名 = トップレベルパッケージ名(アンダースコア)
  でなければならない。setuptools + pip 26 では `name = "lerobot_teleoperator_xxx"` の
  アンダースコアが METADATA の Name にそのまま保持されることを実機確認済み。
- クラス命名: `XxxConfig`(設定)→ `Xxx`(デバイス)。`make_device_from_device_class()` が
  Config クラスのモジュールの**親パッケージ**から `Xxx` を import するので、`__init__.py` で
  Config とデバイスクラスの両方を re-export しておけば確実に解決される。
- 設定クラスは `@TeleoperatorConfig.register_subclass("type名")` + `@dataclass`。
  ベースの `TeleoperatorConfig` は `kw_only=True` dataclass(`id`, `calibration_dir` を持つ)。
- `Teleoperator` サブクラスには `config_class` と `name` のクラス変数が必須。
- インストールは `pip install -e .`。**Jetson では `--no-deps` を付けること**(pip の依存解決が
  numpy 等を勝手にダウングレードし、CUDA ビルドの torch スタックを壊しかねない。実際に
  numpy 2.5.1 → 2.2.6 のダウングレードが発生し、復旧が必要になった)。
- `lerobot-teleoperate` / `lerobot-record` は `main()` の冒頭で `register_third_party_plugins()` を
  呼ぶため、pip install 済みなら `--teleop.type=<type名>` がそのまま使える。

## 2. 座標系 — use_degrees=True がデフォルト(最重要)

- LeRobot 0.6.0 の `so100/so101_follower` と `so100/so101_leader` は **`use_degrees: True` が
  デフォルト**(`config_so_follower.py` / `config_so_leader.py`、「過去のポリシー/データセットとの
  後方互換のため」)。
- つまり関節アクションは **度数**: キャリブレーションの中央 raw 値 `(range_min+range_max)/2` が 0°、
  フルターン(4096カウント)= ±180°。正規化 ±100 ではない。
- グリッパだけは `use_degrees` に関係なく常に `RANGE_0_100`(0=閉、100=開)。
- **±100 でクランプする実装は度数系では「±100°」を意味し、可動域の一部しか使えない**。
  フルターン記録の関節(±180°必要)では約56%で止まり、「半分までしか動かない」症状になる。
- テレオペ側にも `use_degrees` 設定を持たせ、ロボット側と必ず一致させること。

## 3. 関節リミットはテレオペ側の責務(DEGREESはクランプなし)

- `MotorsBus._unnormalize()`(`lerobot/motors/motors_bus.py`)の挙動:
  - `RANGE_M100_100` / `RANGE_0_100`: 入力を ±100 / 0..100 にクランプしてから raw 変換(安全)。
  - **`DEGREES`: クランプが一切ない**。`int(val * 4095/360 + mid)` がそのままサーボに送られる。
    範囲外指令は物理ストッパーに押し付ける動作になる。
- したがって度数モードでは**テレオペ側の関節リミットが唯一の保護**。
  各関節のリミットはロボットのキャリブレーションJSONから導出するのが正確:
  `limit = ±(range_max − range_min)/2 × 360/4095` 度。
  ファイルが無い場合は保守的に ±100° 程度に抑えるのが安全。
- フルターン記録(range 0..4095)の関節は ±180° が座標の端(raw 0/4095)。ただし記録レンジの
  中央が 2047.5 からずれている場合(例 30..4060)、±180° が raw レジスタ範囲 [0,4095] の外に
  変換されるため、`±(180 − |mid−2047.5|×360/4095)` に絞ること。

## 4. キャリブレーションの落とし穴(WRAPPED 0..4095)

- `lerobot-calibrate` のレンジ記録(`record_ranges_of_motion`)は**単純な min/max でラップ検出なし**。
  ホーミング(「middle of range で ENTER」)をアームの休息姿勢のまま行うと、スイープ中に raw
  カウンタが 0/4095 境界をまたぎ、`range_min=0, range_max=4095` という壊れた記録になる
  (SO-101 では shoulder_lift / elbow_flex で頻発)。
- 見分け方: レンジ記録中のライブ表(NAME|MIN|POS|MAX)で **MIN≈0 かつ MAX≈4095 になったら
  ラップ確定**。やり直すこと。JSON 事後チェックでも判定可能
  (`wrist_roll` の 0..4095 だけは仕様 = フルターン関節としてハードコードされる)。
- 防止策: ホーミングの ENTER 前に**全関節を可動域の中央**にする(アームをまっすぐ立てる)。
- **プロンプトの罠**: 既存キャリブレーションがあると「Press ENTER to use provided calibration
  file, or type 'c'...」と聞かれる。**ENTER だけ押すと既存ファイルをサーボへ書き込むだけで
  再記録されない**。再キャリブレーションには `c` + ENTER が必要。
- 再キャリブレーションは正規化座標系を変えるため、**旧キャリブレーションで記録した
  データセット・学習済みモデルとは非互換になる**。運用中のモデルがある場合は要注意。
- リーダー・フォロワー操作は両側の座標系が同じ形に歪むため、壊れたキャリブレーションでも
  一見正常に動く(問題が表面化しない)。

## 5. 巻き付き(円環)制御は実装しないこと

- フルターン記録の関節を「±180 で巻き付いて連続的に動かす」設計(目標値を ±180 でラップ)は
  **実機で破綻する**: 目標が境界を越える瞬間、指令 raw 値が 4094→2 のようにジャンプし、
  STS3215 のファームウェアはこれを「ほぼ1回転の位置誤差」と解釈して**逆方向へ約360°巻き戻す**
  (Phase レジスタでシングルターンモード、加速度制限は実質無効のため全速)。
  SO-101 の実肘で「端まで動かすと反対側の初期位置へ戻る」現象として再現した。
- 正解: **全関節を端(リミット)でクランプして止める**。境界ジャンプが無くなるので
  `--robot.max_relative_target` も安全対策として併用できる
  (`ensure_safe_goal_position` は正規化空間で present±max_diff にクリップするため、
  ラップ実装とは根本的に両立しない、という理由もある)。

## 6. ジョイスティック / evdev 実装の要点(機種非依存)

- Jetson では **evdev 一択**(pygame/SDL 不要、ヘッドレスOK。pygame は未インストールが普通)。
- **機種プロファイル + USB id 自動識別**: 機種で軸配置(右スティックが ABS_RX/RY か
  ABS_Z/RZ か)もレンジ(±32767 か 0..255 か)もトリガー方式(アナログ軸かデジタルボタンか)も
  異なる。意味チャンネル(left_x/left_y/right_x/right_y/dpad_x/dpad_y + グリッパ開/閉)→
  evdevコードの対応表を機種ごとに定義し、`evdev.list_devices()` を走査して
  vendor/product id で自動選択する。機種別の実測値は `reference_devices.md` に集約。
- **スティック正規化は absinfo 基準**: `(raw − center)/half`(center=(min+max)/2)。
  固定の 32767 割りでは 0..255 系パッドが動かない。
- **接続直後の absinfo.value を信用しない**: 0..255 系のHIDパッドは中央静止でも value=0 を
  返すことがあり、そのままシードすると「左上全開」の誤入力になる。スティック/ハットは
  センター値、アナログトリガーは min でシードし、最初のイベント以降に実値を使う。
- 電源が入っていないと `/dev/input/event*` に現れない(スリープするパッドはボタンで起床)。
- 権限: `/dev/input/event*` は `input` グループ。`sudo usermod -aG input $USER` + 再ログイン。
- 読み取りは非ブロッキングで毎フレームドレイン:
  `os.set_blocking(dev.fd, False)` + `read_one()` ループ + `BlockingIOError` 捕捉。
  接続時に `active_keys()` でボタン初期状態をシードする。
- デッドゾーンは再スケール式(`sign×(|v|−dz)/(1−dz)`)にすると出だしが滑らか。
  カーネルの flat 値(例: ELECOM は 15/127.5≈0.12)を覆う程度に設定(0.15 目安)。
- 無線切断(`OSError`)時は**最終指令位置を保持**(ゼロ指令や例外での急停止より安全)。
- 積分制御の dt は上限を設ける(例 0.1s)。ループが一瞬止まっても目標がジャンプしない。
- `connect()` では **reader の生成→start() 成功後に self へ代入**。失敗時に半接続状態が残ると
  `is_connected=True` のままリトライが `DeviceAlreadyConnectedError` になる。

## 7. so101_leader 互換(lerobot-record 対応)の要点

- アクションキーは `<motor>.pos`、**順序も so_leader のモーター順に揃える**:
  `shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper`。
  データセットの特徴はロボット側 `action_features` から構築され、テレオペの辞書は名前で
  引かれる(`build_dataset_frame`)。**全キーを毎回返すこと** — キーが欠けると record 中に
  KeyError。`get_action()` から空辞書 `{}` を返してはならない。
- `connect(calibrate: bool = True)` シグネチャ + `@check_if_already_connected`、
  `disconnect()` + `@check_if_not_connected` で so_leader とガード互換にする。
- `calibrate()`/`configure()` は no-op、`is_calibrated=True` でよい(ベースクラス契約)。
- 積分開始姿勢(initial_position)はデフォルト全関節 0(本体関節はキャリブレーション中央、
  グリッパは 0=全閉)。起動直後の
  初回アクションでアームがそこへ動くため、**`--robot.max_relative_target=5` の併用を推奨**
  (毎ティック present±5 にクリップされ緩やかに到達する)。または initial_position を
  静止姿勢に合わせる。
- 速度は「単位/秒」で定義し、グローバル倍率(`speed_scale`)を持たせると CLI から一発調整できる。
  **SO-101 では elbow_flex が同一指令速度でも体感的に速い**ため、デフォルトを他の 70% 程度に
  下げるとバランスが良い(実機フィードバックより)。

## 7.5 アームのパワーが足りないとき(サーボゲイン)【実機検証済み 2026-07-31】

- LeRobot 0.6.0 の `so_follower.configure()` は**接続のたびに全モーターへ P_Coefficient=16 を
  書き込む**(サーボ工場出荷値は32。震え防止のため半減されている)。これがアームの保持力・
  追従力が弱い主因になる。I=0、D=32 はデフォルト通り。
- 対処(lerobotソースは改変しない): テレオペプラグイン側で `SOFollower.configure` を
  **ランタイムでラップ**し、純正configure完了直後に指定ゲインを上書きする
  (実装例: `lerobot_teleoperator_f710/robot_gains.py`)。フックは設定パース時
  (`__post_init__`)に導入するため、同一プロセス内で robot.connect() より必ず先に入る。
  使い方: `--teleop.robot_gains='{"p_coefficient": 32}'`(teleoperate / record 両対応、
  オプトイン、未指定なら無介入)。**32で関節が唸る/発振する場合は 24 程度に下げる**。
- **グリッパは意図的に弱い**: `configure()` が Max_Torque_Limit=500(50%)、
  Protection_Current=250、Overload_Torque=25% を書き込む(モーター焼損防止)。
  掴む力を上げたい場合はここを緩めることになるが、連続把持での過熱・焼損リスクと引き換え。
- ハード要因も確認: フォロワーの電源は 12V・5A 以上の専用ACアダプタ推奨
  (供給不足は低トルク・シリアル不安定の両方を招く)。
- 追加フィールドはデータセット互換チェック(`robot_type`/`fps`/`features` のみ比較)に影響しない。

## 8. 運用ノウハウ(Jetson / シリアル)

- **テレオペ実行中のシリアルポート(/dev/ttyACM*)に別プロセスから絶対にアクセスしない**。
  診断目的の読み取りでも half-duplex バスが衝突し、
  `SerialException: device reports readiness to read but returned no data (device disconnected or multiple access on port?)`
  でセッションが即死する(実際に発生させてしまった)。
- USB の列挙順(ACM0/ACM1)は再起動・抜き差しで入れ替わる。リーダー/フォロワーの取り違えに注意。
  確実な特定は「対象アームの USB を抜いて `ls /dev/ttyACM*`」または `lerobot-find-port`。
  取り違えると「リーダーのアームがテレオペで動く」「キャリブレーション不一致」等の混乱が起きる。
- 終了時の `Failed to write 'Torque_Enable' ... There is no status packet!` は、バス通信が
  既に死んでいる(ポート衝突・ケーブル・電源)ときの典型症状。

## 9. 検証ツールとテスト観点

- 入力マッピングの目視確認 CLI(例: `lerobot-f710-monitor`)をプラグインに同梱すると、
  ロボット接続前に軸・ボタンの生値を確認でき、切り分けが速い。
- キャリブレーション健全性チェック CLI(例: `lerobot-f710-check-calib`)で
  WRAPPED(0..4095)/ NARROW(スイープ不足)を機械判定できるようにする。
- ユニットテストは evdev リーダーを Fake に差し替えて、以下を固定(pin)する:
  積分の方向と速度 / リミットでの停止(巻き付かないこと)/ speed_scale の効き /
  キャリブレーションファイルからのリミット導出 / 初回アクション(dt=0)での範囲外
  initial_position のサニタイズ / 接続失敗後のリトライ可能性 / アクションキーの順序 /
  デフォルト速度値。
- ハード無しでできる結合確認: `register_third_party_plugins()` → draccus で
  `--teleop.type=...` パース → `make_teleoperator_from_config` → ゲームパッドのみ接続して
  60Hz で `get_action()` ストリーミング(アイドル時に全て初期値で安定すること)。
