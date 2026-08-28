# OtterLeader / RSFollower テレオペプラグイン実装知見

Jetson (LeRobot 0.6.0) + 両腕ヒューマノイド実機(XL330 リーダー + RobStride CAN
フォロワー、16軸)の構築・収録運用(2026-08)で確認した内容。数値はすべて
`plugin/` 同梱コードの実物と一致させてある。

## 1. リーダーの重力ドリフト対策(弱電流の位置保持、2026-08-16 実装・収録運用で使用)

### 問題

7自由度アームの冗長軸 **shoulder_roll(上腕ねじり)** は、手先だけを握る
オペレーターでは拘束されない。前傾姿勢で操作すると肘から先の重量がこの軸まわりの
トルクになり、リーダーが左右対称に「広がる側」へゆっくり落ちる(= フォロワーが
勝手に開いていく)。物理ベルトでの固定は姿勢ごとに巻き直しが必要で使い回しが悪い。

### 解決: XL330 Operating Mode 5 (電流制限付き位置制御) + Goal_Current の弱バネ保持

`OtterLeader.configure()` で肩3軸(shoulder_yaw / shoulder_pitch / shoulder_roll、
ID 1-3 / 11-13)を `OperatingMode.CURRENT_POSITION`(Mode 5)にし、
`Goal_Current` に弱い電流を書いてから `Present_Position` を `Goal_Position` に
書き戻す — **接続時の姿勢を中立として弱いバネで保持**する。

```python
# otter_leader.py configure() の該当部 (グリッパと同じ CURRENT_POSITION 流儀)
self.bus.write("Operating_Mode", motor, OperatingMode.CURRENT_POSITION.value)
self.bus.write("Goal_Current", motor, int(self.config.drift_hold_current_ma))
self.bus.enable_torque(motor)
pos = float(self.bus.read("Present_Position", motor))
self.bus.write("Goal_Position", motor, pos)
```

- **オペレーターは普通に動かせ、読み値 (Present_Position) もそのまま使える**。
  電流制限がトルクの上限になるので、バネに逆らって動かすだけ。
- **位置保持は両方向対称**なので、左右のアームで重力方向の符号が逆でも
  同じ設定でよい(トルクバイアス方式だと左右で符号を分ける必要があった)。
- config (`config_otter_leader.py`):
  - `drift_hold_motor_names`: 対象の **base 名**リスト(side prefix なし)。
    既定 `["shoulder_yaw", "shoulder_pitch", "shoulder_roll"]`。空リストで無効。
  - `drift_hold_current_ma`: 保持電流 [mA](XL330 の Goal_Current 単位 = 1mA)。
    **既定 25**。20〜60mA で調整、0 で無効。CLI 上書き例:
    `--teleop.drift_hold_current_ma=60`。
  - `drift_hold_current_overrides`: **関節別の保持電流上書き**(base 名 → mA)。
    未指定の関節は `drift_hold_current_ma`。既定 `{"shoulder_roll": 18}`。
- **実機調整の経緯**: 40mA で実装 → ユーザー体感「重い」→ **25mA が既定**
  (2026-08-16「もう少し弱く」)。大きいほど強く戻るが操作が重くなる。
  さらに **shoulder_roll のみ 25→18mA に緩和**(2026-08-28 実機フィードバック、
  他の 2 軸は 25mA のまま)— 一律に下げるとドリフトが戻るため関節別上書きで対応。
  18→13mA も試したが**弱すぎた**ため 18 に戻して確定(2026-08-29 実機確認。
  shoulder_roll の適正域は 25=重い / 18=適正 / 13=弱すぎ、と狭い)。
- 起動確認: 接続時に `OtterLeader drift-hold: <motor> を 25mA で現在位置 <pos> に
  弱保持` が 6 行(3軸×左右)出る。
- **wrist_yaw のベルト(DOF5/15)も `drift_hold_motor_names` に `wrist_yaw` を
  足せば置換できる見込み(未検証)**。
- 実装上の注意: `configure()` は「gripper でもドリフト保持でもない関節」だけを
  EXTENDED_POSITION(トルクフリー)にする。`_is_drift_hold_motor()` は
  `drift_hold_current_ma <= 0` のとき常に False を返すので、電流 0 = 完全に従来動作。

## 2. フォロワーの初期位置運用(connect で移動・エピソード間/disconnect で復帰)

### connect(): 初期位置へのブロッキングランプ移動

`RSFollowerConfig.initial_position`(キー = full 名、値 = teleop 単位 0-100)を
設定すると、`connect()` はその姿勢へのランプ移動を完了してから返る。
→ **推論 / teleop の制御ループはアームが初期位置で静止した状態から始まる**。
同梱 YAML の値はデータセット humanoid_test060 の全30エピソード開始姿勢の平均
(2026-08-13 算出。**当時の較正が前提** — 再較正すると同じ正規化値でも
物理姿勢が変わる、§8)。

connect() の順序(`rs_follower.py`):

1. `_prepare_initial_position_ramp()` — **enable 前に** get_angle.py で全軸の
   実角度を読む(リトライ `initial_position_read_retries=2`、タイムアウト
   `initial_position_read_timeout_s=0.5`)。読めない軸があると
   `initial_position_require_feedback=True`(既定)では RuntimeError で起動を止める
   (起動直後の急動作防止)。
2. 各モータの bus connect + enable → カメラ connect → グリッパガード開始
3. `initial_position` があれば `_move_to_initial_position()` でブロッキングランプ

ランプの仕様(`_run_initial_position_ramp()`):

- 所要時間 = max(`initial_position_ramp_duration_s`=**5.0 秒基準**,
  max_delta / `initial_position_ramp_max_speed_rad_s`=**0.5 rad/s**)。
  つまり大きく離れていると 5 秒より延びる。
- 送信間隔は `initial_position_ramp_interval_s`(null なら 1/tx_hz = 0.02s)。
- 最初の 1 フレームは**現在角度そのもの**を目標に書く(急な位置誤差を作らない)。
- **ランプ省略**: 目標との最大差が `initial_position_ramp_skip_within_rad`
  (既定 **0.03 rad**)以下なら即書き込み。これは推論の
  `return_to_initial_position` 後にもう一度 5 秒待つ「二重待ち」の防止。

### disconnect(): トルク切断前に初期位置へ復帰(2026-08-16 実装)

`disconnect()` の先頭(トルク切断前・ガード停止前)で
`_return_to_initial_position()` を呼ぶ — connect と同じランプ機構で、
**実フィードバックを再取得してから**(`_prepare_initial_position_ramp()`)
現在姿勢 → initial_position へゆっくり戻り、それからトルクを切る。

- テレオペ終了時のどんな姿勢からでも滑らかに戻る(最後の目標値ではなく
  実角度を読み直すのが要点)。
- **失敗時は警告して disconnect 続行**(CAN 断などの異常系でハングさせない。
  `logger.exception` + ランプスキップ)。
- config: `return_to_initial_on_disconnect`(既定 **True**)。
  `initial_position` 未設定または `initial_position_ramp_enabled=False` なら何もしない。
- targets の計算は `_initial_position_targets()` に抽出されており、
  connect/disconnect で共通(未指定の関節は現在角度を維持)。

### エピソード間の自動初期位置復帰(2026-08-28 実装、test070 収録で使用)

収録のリセット区間ごとにフォロワーを初期位置へ戻す仕組み。2 つの部品から成る:

1. **`RSFollower.return_to_initial_position()`(public メソッド)**: 現在の指令位置
   から `initial_position` へ connect/disconnect と同じランプ機構でゆっくり移動する。
   - **開始点は直近の指令値 (`_last_targets`)** — disconnect 時と違い実フィードバック
     を読み直さない。即座に開始でき、指令の連続性が保たれるため座標系ずれも
     発生しない(§6 の事故モードに入らない)。
   - 失敗しても `logger.exception` して**収録は続行**する。
   - 復帰ランプ中の Ctrl+C は**ログして再送出**(握りつぶすと停止意図が失われ、
     送出したままだと disconnect が走らずトルクが入ったままプロセスが落ちる)。
2. **`lerobot_record.py` への site-packages パッチ**: リセット区間の頭
   (`log_say("Reset the environment", …)` 直後)に
   `if hasattr(robot, "return_to_initial_position"):` で呼び出す(ブロッキング ~5秒)。
   復帰中の Ctrl+C は `return_to_initial_on_disconnect=False` にしてから
   `robot.disconnect()` + 再 raise(二度目の復帰をしない即トルク断)。
   - **リセット区間はテレオペ遮断で原点保持(2026-08-29 追加)**: 復帰後の
     リセット区間は `record_loop()`(テレオペ送信あり)ではなく単純な待機ループに
     置き換えた — **次の「Recording episode N」アナウンスまで、リーダーを
     動かしてもフォロワーは初期位置から動かない**。リセット中の構え直しで
     フォロワーが釣られて動く問題の解消。キー操作(次へ/再収録/停止)は有効。
   - パッチ対象: `<venv>/lib/python3.12/site-packages/lerobot/scripts/lerobot_record.py`
     (バックアップ `.bak.epreset`(復帰のみ)と `.bak.holdreset`(原点保持前)を
     同ディレクトリに退避)。
   - **venv 再構築時は要再適用**。hasattr 検出なので、パッチが無くても
     壊れはしない(復帰しなくなるだけ)。他ロボットにも無害。
- 運用値: **`--dataset.reset_time_s=5` 推奨** — 復帰ランプ ~5 秒(ブロッキング、
  reset_time_s の外)+ reset 区間 5 秒で物品の再配置とリーダーの構え直しをする。
- 復帰完了ログ「初期位置への復帰完了 — 物品を配置し、リーダーを初期位置に
  構えてください」が次エピソードの準備合図。

## 3. USB シリアル (FTDI) の EMI 瞬断対策

リーダーの USB シリアルは EMI 等で瞬断することがある(カーネルログは
`error -71`)。十数秒で自動再列挙されるので、プラグインとスクリプトの両方で吸収する:

- **connect の 1 回リトライ**(`otter_leader.py connect()`): `OSError` /
  `termios.error` を捕まえ、bus を安全に disconnect →
  `_wait_for_port(timeout_s=20.0)` でポートファイルの復帰を 0.5 秒間隔でポーリング →
  復帰後 1.0 秒待って(再列挙直後の安定待ち)再接続。2 回失敗で
  `DeviceNotConnectedError`(ポート名と直近エラーを含むメッセージ)。
- **disconnect も防御**: 瞬断後のポート操作は I/O error になるので、切断時の
  シリアル例外は警告に格下げ(二次トレースバックで本来のエラーを隠さない)。
- **ポートの決め打ちをやめる**: 再列挙で `/dev/ttyUSB0` → `/dev/ttyUSB1` に
  変わることがある。起動スクリプトは
  `LEADER_PORT=$(ls /dev/ttyUSB* 2>/dev/null | head -1)` で自動検出する。
- 収録が途中で落ちた場合は `lerobot-record --resume=true` で既存エピソードを
  保持したまま続きから収録できる(実績: 30ep 収録中に 1 回中断 → resume で完走)。

## 4. LeRobot 0.6.0 プラグイン規約とキャリブレーション互換

### 発見と命名(カメラプラグインと同一の仕組み)

- `register_third_party_plugins()` が配布名 `lerobot_teleoperator_` /
  `lerobot_robot_` プレフィックスの配布物を**配布名のまま import** する。
  → 配布名 = トップレベルパッケージ名(アンダースコア維持)。`--plugins` フラグは無い。
- クラス解決は `XxxConfig` → `Xxx`(`OtterLeaderConfig`→`OtterLeader`、
  `RSFollowerConfig`→`RSFollower`)。`__init__.py` での re-export が最短経路。
  - rs_follower の `__init__.py` は **config を即時 import + 本体を
    `__getattr__` で遅延 import** する。発見時の import は軽量でなければならず、
    python-can 等の実行時依存が無い環境でも `rs_follower` タイプ自体は登録される。
- `@TeleoperatorConfig.register_subclass("otter_leader")` /
  `@RobotConfig.register_subclass("rs_follower")` + `@dataclass`。
  実装クラスには `config_class` と `name` のクラス変数。
- インストールは **`pip install -e . --no-deps`**(Jetson の CUDA torch/numpy 保護)。
  依存は手動: リーダー = `dynamixel-sdk` + `deepdiff`(lerobot MotorsBus が要求)、
  フォロワー = `python-can` + `PyYAML`。
- コード本体は 0.4.0 から**変更ゼロ**で 0.6.0 互換だった(Teleoperator /
  Robot の抽象メソッドが一致)。移行作業は setup.py → pyproject.toml 化と
  deps 整理のみ。

### キャリブレーション互換(0.4.0 → 0.6.0)

**0.4.0 時代の JSON がそのまま読める**(実機で検証済み、再キャリブ不要):

- リーダー: `~/.cache/huggingface/lerobot/calibration/teleoperators/otter_leader/<id>.json`
  (16 モータ、lerobot 標準の MotorCalibration 形式)
- フォロワー: `~/.cache/huggingface/lerobot/calibration/robots/rs_follower/<id>.json`
  (独自形式 version 20: `ranges: {<full_name>: {open_rad, close_rad}}` +
  teleop_min/max。旧 6DOF の `<side>_<legacy>_open_rad` フラットキーからの
  自動移行 `_load_legacy_calibration()` 付き。保存先は config の
  `rs_calibration_subdir` = `calibration/robots/rs_follower`)

### YAML config の読み込み優先順位(RSFollower)

`__init__` 冒頭の `_apply_yaml_config_overrides()`:
1. `cfg.config_file` → 2. 環境変数 `RS_FOLLOWER_CONFIG` →
3. パッケージ同梱 `configs/rs_follower_7dof_gripper.yaml`(`load_default_yaml=True` のとき、
   ファイル名は `config_name`)。`robot:` ネスト対応、未知キーは警告して無視。
   同梱 YAML は `[tool.setuptools.package-data]` で配布物に含める。

### ID マップ表(既定構成の正)

**リーダー (Dynamixel XL330-M288)** — shoulder→gripper の昇順:

| 関節 (base名) | LEFT ID | RIGHT ID | Norm mode |
|---|---|---|---|
| shoulder_yaw | 1 | 11 | RANGE_M100_100 (+drift hold) |
| shoulder_pitch | 2 | 12 | RANGE_M100_100 (+drift hold) |
| shoulder_roll | 3 | 13 | RANGE_M100_100 (+drift hold) |
| elbow_pitch | 4 | 14 | RANGE_M100_100 (Drive_Mode 反転) |
| wrist_yaw | 5 | 15 | RANGE_M100_100 |
| wrist_pitch | 6 | 16 | RANGE_M100_100 |
| wrist_roll | 7 | 17 | RANGE_M100_100 |
| gripper | 8 | 18 | RANGE_0_100 / CURRENT_POSITION |

**フォロワー (RobStride)** — gripper→shoulder の昇順(**リーダーと逆順**なので注意):

| 関節 (base名) | LEFT ID | RIGHT ID | モデル |
|---|---|---|---|
| gripper | 0x01 | 0x11 | RS05 |
| wrist_roll | 0x02 | 0x12 | RS05 |
| wrist_pitch | 0x03 | 0x13 | RS05 |
| wrist_yaw | 0x04 | 0x14 | RS05 |
| elbow_pitch | 0x05 | 0x15 | RS00 |
| shoulder_roll | 0x06 | 0x16 | RS00 |
| shoulder_pitch | 0x07 | 0x17 | RS06 |
| shoulder_yaw | 0x08 | 0x18 | RS03 |

- **right_shoulder_yaw は 0x18**。0x08 にすると left_shoulder_yaw と重複し、
  `_validate_unique_ids()` が ValueError を出す(左右同一 CAN bus のため)。
- RS06 だけ専用の送信ループ実装(`RS06Bus`、独自の enable/制御フレーム)、
  RS00/03/05 は `RobStrideBus`。

### 値の変換規約(16 キーの対)

- リーダー `get_action()`: gripper は正規化値 0〜100 のまま、その他は
  `[-100,100] → [0,100]`(`(norm+100)*0.5`)。
- フォロワー `send_action()`: teleop 値 0-100 をキャリブ済み `open_rad..close_rad`
  へ線形マップ(`teleop_min=0 / teleop_max=100`)。`max_relative_target`(既定 None)
  でステップ制限も可能。
- 旧名エイリアス(`wrist_flex`→`wrist_pitch`、`elbow_flex`→`elbow_pitch`、
  `shoulder_lift`→`shoulder_roll`、`shoulder_pan`→`shoulder_pitch`)は
  action 読み取り・キャリブ読み込みの両方でフォールバックされる。

## 5. 収録運用(実測に基づく規則)

### 毎フレームログは DEBUG に置く

プラグインの毎フレームログ(リーダーの各モータ値、フォロワーの CMD teleop→targets)
を INFO で出すと、**制御ループが 0.2Hz まで減速した**(16軸×60fps のログ I/O)。
両プラグインとも `logger.debug` に変更済み。新しいログを足すときも毎フレーム経路は
必ず DEBUG。

### 二重起動ガード

同じ CAN バス/シリアルポートに 2 プロセスが繋がると挙動が壊れる
(学習ならチェックポイント破損)。起動スクリプトの事前チェックに入れる:

```bash
if pgrep -f "[l]erobot-(record|train|rollout|teleoperate)" >/dev/null; then
    echo "NG: 別の lerobot プロセスが実行中です → 先に終了してください"; exit 1
fi
```

### グリッパの過電流/トルクガード(RS05、v0.0.17 で確定)

物体に触れるまでの自由閉じ速度は制限せず、保持力だけを制御する多層ガード。
**以下の数値は v0.0.17 当時の初期調整値(= dataclass 既定)**。実運用の閾値は
同梱 YAML で 4.0N・m 系(2026-08-28)→ **3.2N・m 系(2026-08-29、現行)**へ
再調整済み — 次項「グリッパ把持予算の実機調整」の表が現行の正:

- **motor 側ハード上限(最優先)**: enable 前に RobStride `limit_torque` (0x700B) に
  `gripper_max_torque_nm=3.0` を書き込み**読み戻し検証**
  (`gripper_torque_limit_required/verify=true`、検証失敗で起動拒否)。
  同様に `limit_cur` (0x7018) = 6.5A。
- **監視**: type-2 status フレーム(トルク/温度/フォルトフラグ)を常時受信 +
  `iqf` (0x701A、フィルタ済み q 軸電流) を `gripper_current_monitor_hz=20.0` で
  ポーリング。status が古い時の**角度フォールバックは get_angle.py 20Hz**
  (`gripper_guard_feedback_hz=20.0`)。
- **接触検出 → トルク制限付き保持**: 接触後は 2.20N・m から 3.0N・m へ
  3.0N・m/s でランプ(`gripper_contact_initial_torque_nm=2.20` /
  `gripper_torque_ramp_nm_s=3.00`)。保持は「要求トルク/Kp」の位置誤差窓に変換
  (Kp=20, 3.0N・m → 0.15rad が絶対上限 `gripper_guard_max_hold_error_rad`)。
- **ハードトリップ**: torque≥3.15N・m ×3 サンプル、電流≥6.2A ×2 サンプル、
  または過電流/stall/フォルトフラグ → **0.05rad 開いてラッチ**
  (`gripper_overcurrent_backoff_rad=0.05`)、1.5 秒クールダウン後に
  オペレーターが明示的に開くと解除(`gripper_overcurrent_latch_until_open=true`。
  **現行 YAML は false** — 次項参照)。
- **RS05 のトルク換算は ±5.5N・m**(公式仕様)。v0.0.16 以前は RS02 の ±17N・m を
  誤用しており、読みが約 3.09 倍大きく、ガードが要求よりはるかに弱い力で作動していた。
  status 換算表は `robstride_bus.py` の `STATUS_LIMITS`
  (RS00: t=17.0 / RS03: t=60.0 / RS05: t=5.5)が正。
- フィードバック欠落時はフルトルクへ上げない
  (`gripper_require_status_for_full_torque=true`、上限 1.80N・m)。

### グリッパ把持予算の実機調整(2026-08-28 → 08-29 で確定)

収録実運用で「保持力が足りず物体が滑る」→ 把持予算を 3.0 → 4.0N・m 系へ
引き上げた(2026-08-28)が、今度は**収録中に「すぐロックして手が死ぬ」が頻発**。
ソフトしきい値の緩和(rs-follower `2151e49`)では直らず、テレメトリの実測で
真因を確定して **3.2N・m 系へ再調整**した(`f1207f2`、現行)。値は**同梱 YAML
(`configs/rs_follower_7dof_gripper.yaml`)側**で上書きしている
(dataclass 既定と `examples/` の YAML は v0.0.17 の 3.0N・m 系のまま)。

**「すぐロック」の真因はモータ内部の堵転(ストール)保護**
(`outputs/gripper_guard.log` + テレオペログの実測で確定):

- 4.0N・m 保持の定常電流 ≈6.8A が数秒持続すると、RS05 **自身の堵転保護**が
  overcurrent フラグ→フォルトを立てる(`limit_cur` 8.0A より低い電流で発火する
  別系統の保護。プラグインのソフトしきい値をいくら緩めても防げない)。
- フォルト後は**フィードバックが途絶**するため、「健全なステータス/電流」を
  要求するガードの解除条件が永遠に満たせず、**再接続までハンドが死ぬ**。

対策(3本柱、rs-follower `f1207f2` / `f802787`):

1. **把持トルク 4.0 → 3.2N・m**: 定常電流 ≈5.2A(実測)で堵転保護の発火域外。
2. **フォルト自動復旧 `RobStrideBus.try_fault_recovery()`**: RobStride 私有
   プロトコルの Type4 (STOP) `data[0]=1` = 故障クリア → 再イネーブルの順に送る
   (RAM のトルク/電流上限は電源断まで保持されるので再設定不要)。ガードは
   ラッチ中に「開き操作 + クールダウン後もモータ沈黙/フォルト継続」を検出すると
   これを自動試行する(1 秒レート制限)。復活すれば新鮮なステータスが届き
   通常の解除経路が機能する。
3. **接触ホールドの入り/解除を緩和**: `gripper_contact_torque_nm` 1.0→**1.5**
   (軽接触で即ホールドに入らない)、`gripper_guard_release_rad` 0.12→**0.05**
   (少し開けばすぐ解除 — 従来は可動域の約 10% 開くまで無反応で「手が死ぬ」感覚)。

| パラメータ | v0.0.17 | 08-28 (4.0系) | **現行 08-29 (3.2系)** |
|---|---|---|---|
| `gripper_max_torque_nm` (limit_torque 0x700B) | 3.0 | 4.0 | **3.2** |
| `gripper_torque_soft_limit_nm` | 2.85 | 3.8→4.0 | **3.2** |
| `gripper_torque_hard_limit_nm` | 3.15 | 4.3 | **3.6** |
| `gripper_torque_release_nm` | 2.70 | 3.6→3.7 | **2.90** |
| `gripper_contact_torque_nm` | 1.00 | 1.00 | **1.50** |
| `gripper_contact_initial_torque_nm` | 2.20 | 2.80 | **2.60** |
| `gripper_no_status_max_torque_nm` | 1.80 | 2.40 | 2.40 |
| `gripper_hardware_current_limit_a` (limit_cur 0x7018) | 6.5 | 8.0 | 8.0 |
| `gripper_current_soft_limit_a` | 5.5 | 7.0→7.2 | 7.2 |
| `gripper_current_hard_limit_a` | 6.2 | 7.5→7.8 | 7.8 |
| `gripper_current_release_a` | 5.0 | 6.0→6.2 | 6.2 |
| `gripper_current_trip_confirm_count` | 2 | 2→3 | 3 |
| `gripper_overcurrent_backoff_rad` | 0.05 | 0.05→0.04 | 0.04 |
| `gripper_overcurrent_cooldown_s` | 1.5 | 1.5→0.8 | 0.8 |
| `gripper_overcurrent_latch_until_open` | true | false | false |
| `gripper_guard_release_rad` | 0.12 | 0.12 | **0.05** |

- **鉄則: トルク予算を変えたら電流予算も釣り合わせる**。RS05 の実測換算は
  **~1.6〜1.7A/N・m**(実測: 3.2N・m 保持時 iqf ≈5.2A / 3.74N・m 時 6.28A)。
  ただし電流上限(limit_cur)の釣り合いだけでは不十分 — **堵転保護は
  limit_cur より低い持続電流で発火する**ため、把持予算そのものを保護域外
  (定常 ≈5.4A 以下)に置くのが本質的な対策。
- **`latch_until_open=false` の理由**: true だと、トリップのたびにオペレーターが
  `gripper_guard_release_rad` 以上大きく開き直すまで閉じがブロックされ、
  収録のテンポが崩れる。false ではトリップ後クールダウン
  (`gripper_overcurrent_cooldown_s`)+ 健全なステータス/電流を条件に、
  通常の開き操作だけでラッチが解除され把持に復帰できる。モータ側の
  `limit_torque`/`limit_cur` ハード上限は常時残るため安全側は崩れない。
- **ガードイベント + テレメトリの恒久記録**: ガードのトリップ/接触検出等は
  `/home/jetson/Otter/outputs/gripper_guard.log` に追記される
  (ホスト/コンテナ共有、端末を閉じても事後解析できる)。実測調整のため
  `gripper_current_log_enabled: true` / `interval 0.5s` で電流・トルクの
  TELEM 行も同じファイルに記録中。しきい値をいじる前に必ずこのログで
  実電流を確認すること。

### OtterTools ディスプレイ統合(2026-08-29)

ロボット前面の状態表示ディスプレイ([FaBoAI/OtterTools](https://github.com/FaBoAI/OtterTools)、
ESP32-S3 丸型 LCD)。USB CDC `/dev/ttyACM0` に**改行区切り 1 行 JSON** を書くだけで
状態を表示できる(プラグイン本体ではなく起動スクリプト側の統合):

- **`tool/display_status.sh <state>`**(`/home/jetson/Otter/tool/`):
  状態プリセット `docker/record/teleop/infer/calib/idle/train` + `raw '<JSON>'`。
  IP アドレスを毎回自動取得して上部に表示。**ディスプレイ未接続なら黙って成功終了**
  する(呼び出し元スクリプトを失敗させない)ので、無条件に挿入してよい。
- **`tool/display_idle_daemon.py`**: 起動 60 秒後(`--startup-delay`)に待機画面へ。
  lerobot 系プロセス実行中は画面に触らず、終了検出で待機画面へ戻す
  (スクリプト側 trap の保険)。待機中のみ QDD 16 台 (0x01-0x08/0x11-0x18) を
  **GET_ID(通信タイプ 0、読み取り専用)**でスキャンし生存を丸表示 —
  モータ状態は一切変更しない。実行中判定に掛かっている間は CAN に触らない。
- 収録/推論/学習の全 run スクリプト(record 3 + infer 7 + train 10 = 20 本)に
  `trap '… idle' EXIT` + 開始時の状態表示を挿入済み。

### その他の運用規則

- **CAN 設定**: bitrate 1000000, `restart-ms 100`, `txqueuelen 1000`(can0_on.sh)。
- **fps**: teleop 単体は 60、収録は `--dataset.fps=30`(カメラ 30fps に合わせる)。
- **ヘッドレスでは `--display_data=false` 必須**(rerun のチャネル詰まりで
  収録ループがブロックする)。
- **音声ガイド: `--play_sounds=true`**(2026-08-28 から採用、test070 収録で使用)。
  lerobot の `log_say()` が `spd-say`(speech-dispatcher)で
  「Recording episode N」「Reset the environment」等を読み上げる(USB スピーカー)。
  画面を見ずにエピソードの**開始タイミングが掴めない問題を解消**した。
  Jetson では `spd-say` が入っていることを確認(`speech-dispatcher` パッケージ)。
- 収録前チェックリスト(スクリプトに組み込む): can0 operstate=up /
  `/dev/ttyUSB*` 存在 / (HSB カメラ併用時) mgbe0_0 carrier=1 / 二重起動なし。
- get_observation() はフォロワーの実角度ではなく**最後に送った目標値**を
  teleop 単位に戻して返す(RobStride の全軸同期読みが無いための設計。
  グリッパのみ type-2/get_angle.py の実フィードバックがガードに使われる)。

## §6 事故記録: 多回転座標系ずれによる全周回転・断線 (2026-08-27) と安全装置

### 事象

再キャリブレーション+電源サイクルを行った日のテレオペ終了時、切断時の初期位置
復帰で right_shoulder_roll (0x16, RS00) が**突然一回転し、配線を巻き込んで切断**した。

### 原因の連鎖

1. RobStride の位置座標は **±4π (±2回転) の多回転絶対値** (`MODEL_LIMITS["RS00"]["p"] = ±12.57`)。
   電源サイクル後のカウンタ初期化により、**同じ物理姿勢が ±2π ずれた値で報告され得る**
2. 較正 (open/close) と initial_position は「較正セッションの座標系」の絶対値
3. 初期位置ランプは現在角→目標を直線補間するため、座標系が 2π ずれていると
   **「同じ姿勢へ、ぐるっと一回転して到達する」軌道**を速度上限 0.5 rad/s で忠実に実行する
4. 折り返し正規化・総移動量チェックが存在しなかった (connect 時の初期位置移動にも同じ潜在バグ)

### 対策の変遷 (2026-08-27 に2段階で確定)

**第1版 (撤回)**: 「wrap 正規化」— 目標を現在角と同じ回転周の最近傍表現へ写す。
同日夜、**この正規化自体が2件目の誤回転を起こした**: right_wrist_yaw (0x14) の
開始角が較正レンジの +2π 外にあったとき、正規化は「可動域の外へ巻く」+72° の
軌道を選択した (正しくは動かないべき状況だった)。

**確定版 (現行)**: **±2π の座標系オフセットと物理的な巻き込みは、数値だけでは
原理的に区別できない** (表現が同一) — 区別できない状況では動かさない、が唯一の安全策。

- **レンジ整合チェック** (`initial_position_range_check`, 既定 on):
  開始角が較正レンジ ±0.5 rad (`initial_position_range_margin_rad`) の外にある
  関節が1つでもあれば**一切動かずに中止** (disconnect 時=警告してトルク断続行 /
  connect 時=接続中断)。メッセージが目視確認・手動復帰・再キャリブレーションを案内
- **移動量ガード**: 関節ごとに max(`initial_position_max_travel_rad`=1.6, レンジスパン+0.5)。
  レンジ内の大移動 (広可動域の手首系はスパン ~3.5 rad) は通常テレオペと同等なので許可
- 事故2件のシナリオがいずれも「不動作で拒否」になることを数値検証済み

### 教訓

- 較正由来の絶対角度を無検証でモータに送らない。多回転対応モータでは「同じ姿勢」に複数の表現がある
- **曖昧さを「賢く」解決して動かすより、曖昧なら動かない方が常に安全** —
  自動復帰のような無人動作は特に、失敗モードが「止まる」側に倒れる設計にする
- 座標系不整合の主因は電源サイクル (カウンタ再初期化)。再キャリブレーション直後や
  電源入れ直し直後の最初の接続はレンジ外中止が出やすい — 正常な安全動作であり、
  関節を可動域中央付近にして電源を入れ直すと解消する

## §7 グリッパ反転の診断手順 (2026-08-27 実例)

「リーダーを閉じるとフォロワーが開く」症状の切り分け:

1. **リーダーの読み値をデータセット規約と照合** (`tools/check_gripper_direction.py`):
   手を開いた/閉じた状態の teleop 値を、データセット収録時の基準
   (例: 開 = left≈86 / right≈9) と比較。一致していればリーダーはシロ
2. **フォロワーの較正ファイルを確認**: `inverted_motor_names` は**較正時に**
   open=max/close=min の割り当てとして焼き込まれ、実行時は較正ファイルの
   open_rad/close_rad だけが使われる。物理的な「開」が min/max のどちら側かは
   機構依存で、**サーボ整備で反転し得る**
3. **修正は必ず「リーダー読み値の規約 = データセット規約」を保つ側で行う**:
   フォロワーの較正ファイルの open/close を入れ替え (+ 将来の再較正のため
   YAML の inverted_motor_names も修正)。フォロワーの規約自体を変えると
   過去のデータセット・学習済みポリシーが全て逆動作になる
4. リーダー側が反転した場合の保険: `flipped_gripper_names` (読み値を 100-v に補正)

## §8 再較正 = 座標系の再定義 (2026-08-28) — データセット/モデル互換性

### 再較正は「壊れたら直す」作業ではない

フォロワーの較正 (open_rad/close_rad) は **正規化 0-100 の物理的意味そのもの**を
定義している。較正をやり直すと同じ正規化値が指す物理姿勢が変わるため:

- **旧較正で収録したデータセット・学習済みモデルと非互換になる**
  (実例: 再較正後、test060 系データセットで学習したモデルの動きが
  現座標系で数度ズレる)
- YAML `initial_position` の正規化値も**物理姿勢が変わる**
  (同梱 YAML の値は test060 収録時の較正が前提)
- §7 のグリッパ反転も再較正の副作用として起き得る

**収録シリーズの途中で再較正しない**こと。やむを得ず再較正したら、それ以降は
新シリーズとして扱い、旧モデルの推論には座標ズレを疑う。

### 座標系ずれの有無の判定方法 (実績あり)

- **使える: 映像テンプレートマッチング** — 両日のデータセットから
  「関節状態 (正規化値) がほぼ一致するフレーム」を探し、カメラ映像の
  アーム位置をテンプレートマッチングで比較する。実測でずれ 3px = 座標系ずれ無し
  と判定できた (カメラ・ロボット双方が固定されている前提)。
- **使えない: 較正端点の手押し再測定** — 端まで手で押し付ける測定は
  スイープ圧のかけ方で **±0.1 rad ばらつく**ため、数度レベルのずれの
  判定には使えない。
- 補足: RobStride は**トルクオフ後に自重でストッパーへもたれる**ため、
  接続時の読取角が較正端の少し外側にあっても正常
  (§6 のレンジ整合チェックはマージン ±0.5 rad でこれを許容している)。
