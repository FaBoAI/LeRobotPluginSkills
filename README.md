# LeRobotPluginSkills

LeRobotプラグイン開発のための **Agent Skills** です。

各種入力デバイス・カメラを [LeRobot](https://github.com/huggingface/lerobot) のサードパーティプラグイン(テレオペレーター/カメラ)として対応させるための、コード生成ワークフロー・実装知見・デバイス実測データをスキル形式でまとめています。Claude Code などのコーディングエージェントに読み込ませて使います。

## スキル一覧

### [joystick-teleop-skills](./joystick-teleop-skills/) — ジョイスティック

スティック入力を関節目標位置に**積分**する速度ベース制御。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| Logitech F710 | USB `046d:c21f`(XInputモード) | スティック±32767、LT/RTアナログトリガー。前面スイッチは「X」 |
| ELECOM JC-U3912T / JC-U3812T | USB `056e:200e` | スティック0..255、右スティックABS_Z/RZ、省電力モードの罠あり |

### [midi-teleop-skills](./midi-teleop-skills/) — MIDIコントローラ

フェーダー位置を関節角度に直接マップする**絶対位置**制御(ソフトテイクオーバー付き)。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| SMC-Mixer | ALSAカード名 `SINCO` | Mackie Control方式(フェーダー=PitchBend)。Masterポート(サブデバイス1)使用 |

### [spacemouse-teleop-skills](./spacemouse-teleop-skills/) — SpaceMouse(6自由度)

6DoFパックの変位を関節速度として積分する**6軸同時**の速度制御。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| 3Dconnexion SpaceMouse Compact | USB `256f:c635` | リリース時ゼロイベントなし→ホールドタイムアウトで暴走防止。ボタン2個はグリッパ開/閉 |

### [quest-teleop-skills](./quest-teleop-skills/) — Meta Quest(VR / WebXR)

コントローラ6DoFポーズによる**絶対位置IK制御**(手の動きにグリッパが1:1追従)。ヘッドセットへのインストール不要(WebXR+内蔵HTTPSサーバ)。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| Meta Quest 3 | WebXR(同一WiFi) | パススルーで実機を見ながら操作。クラッチ式(グリップ=原点)、IKはURDF実寸・placo不要。joint_offsets_deg較正手順あり |

### [otter-rs-teleop-skills](./otter-rs-teleop-skills/) — 両腕ヒューマノイド リーダー/フォロワー

Dynamixel XL330 リーダー + RobStride CAN フォロワー(7DOF+グリッパ×左右=16軸ペア)の**テレオペレータ/ロボット両プラグイン**。リーダーの重力ドリフト対策(弱電流保持)・フォロワーの初期位置ランプ・グリッパ過電流ガードを含む。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| OtterLeader (FaBo, XL330-M288 ×16) | USB シリアル `/dev/ttyUSB*` (FTDI) | ID L:1-8 / R:11-18。肩3軸は Mode 5 + Goal_Current 25mA(shoulder_roll のみ 18mA)の弱バネ保持(ドリフト対策)。EMI 瞬断 (error -71) は connect リトライで吸収 |
| RSFollower (RobStride RS00/03/05/06 ×16) | SocketCAN `can0` (1Mbps) | ID L:0x01-0x08 / R:0x11-0x18(リーダーと逆順)。connect/disconnect で初期位置へランプ移動(最大0.5rad/s)。グリッパは limit_torque 3.2N・m 検証 + 電流 20Hz 監視 + フォルト自動復旧 |

### [hsb-camera-skills](./hsb-camera-skills/) — Holoscan Sensor Bridge カメラ(10GigE)

hololink 専用 venv のワーカープロセス + /dev/shm seqlock ブリッジによる**プロセス分離型カメラプラグイン**(LeRobot 側の Python/numpy と衝突しない)。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| Leopard Imaging VB1940 Eagle | BOOTP (UDP 12267)、MAC `8c:1f:64` | 10GigE PoE 直結。2560×1984@30 実測 30.2fps。カメラは要冷却(発熱でリンク断)、復帰は mgbe down/up。`hololink.reset()` 禁止。露光/ゲイン調整(`exposure`/`analog_gain`)と任意解像度への縮小出力(`width`/`height`)対応 |

### [gemini305-camera-skills](./gemini305-camera-skills/) — Orbbec Gemini 305(USB ステレオデプス)

pyorbbecsdk (Orbbec SDK v2) を LeRobot と同一プロセスで直接使う**デプス対応カメラプラグイン**(RealSenseCamera 互換 API、depth は `(H,W,1)` uint16 mm)。

| 対応機種 | 識別 | 備考 |
|---|---|---|
| Orbbec Gemini 305 | USB `2bc5:0840` | 手首搭載向け超小型ステレオ。1280×800@30 color+depth 実測 30.3fps。1280×800/720 の 60fps は MJPG のみ(実測 60.3fps)、848×530 以下は無圧縮 60fps 可。udev ルール必須。PyPI は `pyorbbecsdk2` を使う(無印 `pyorbbecsdk` は v1 の罠)。デフォルトプロファイルは 848×530@30 |

## 各スキルの構成

```
<skill>/
├── SKILL.md                     # エージェント向けワークフロー(疎通確認→キャプチャ→コード生成→動作確認)
└── reference/
    ├── reference.md             # 実装知見(座標系・制御設計・落とし穴)
    ├── reference_devices.md     # デバイス別の実測プロトコル + 新機種対応手順
    └── reference_URL.md         # LeRobot規約・ソース該当箇所へのポインタ(joystickのみ)
```

## 検証済み環境

- LeRobot **0.6.0**(サードパーティプラグイン規約準拠、`pip install -e .` で `--teleop.type=<name>` / `--robot.cameras` の `"type"` が使用可能に)
- ロボットアーム: **SO-101** フォロワー(teleop系)/ **RobStride 両腕ヒューマノイド**(otter-rs-teleop、16軸)
- プラットフォーム: NVIDIA **Jetson Orin Nano**(テレオペ系、ヘッドレス、追加pip依存なしのevdev / ALSA raw MIDIバックエンド)/ NVIDIA **Jetson AGX Thor**(hsb-camera、JetPack 7 + Holoscan SDK 3.6 deb、Dockerレス)/ NVIDIA **Jetson**(gemini305-camera、aarch64 + Python 3.13 conda、pyorbbecsdk2 ホイール)
- いずれも実機で動作検証済み(可動域・イベントボタン・`lerobot-record` 互換 / カメラは実カメラ 30fps 連続取得)

## 新しいデバイスの追加

各スキルの `reference/reference_devices.md` 末尾にある「新しいデバイスを対応させる手順」に従ってください(ケイパビリティ/プロトコルの実測 → プロファイル定義 → モニタCLIで確認 → 実測データを追記)。
