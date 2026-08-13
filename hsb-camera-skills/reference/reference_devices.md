# 対応 HSB カメラ デバイス別リファレンス

## Leopard Imaging VB1940 Eagle(type: `hsb`)【実測済み 2026-08】

10GigE PoE 直結カメラ(センサーブリッジ基板不要)。ステレオ(左右 + IMU)を
**単一の IP / 単一ポート**で送る。

### 識別

| 項目 | 値 |
|---|---|
| MAC プレフィックス | `8c:1f:64`(実測個体: `8c:1f:64:6d:70:a4`) |
| デフォルト IP | 192.168.0.2(**電源断で揮発** — set-ip デーモンが再適用する方式) |
| enumeration | UDP 12267 への BOOTP ブロードキャスト(非特権で受信可) |
| FPGA IP version | 実測個体 0x2507(hsb_v2.5.0 の要求は v2510 — 更新は `program_leopard_cpnx100` + manifest、最大30分・中断厳禁) |

### カメラモード(`camera_mode`)

| mode | 解像度 | fps | フォーマット | 1フレーム(RGB8換算) |
|---|---|---|---|---|
| 0 | 2560×1984 | 30 | RAW10 | 15.2MB |
| 1 | 1920×1080 | 30 | RAW10 | 6.2MB(収録推奨) |
| 2 | 2560×1984 | 60 | RAW10 | 15.2MB |
| 3 | 2560×1984 | 30 | RAW8 | 15.2MB |

処理チェーン(ワーカー内): LinuxReceiver(UDP) → CsiToBayer → ImageProcessorOp
(optical_black=8, ゲイン r=1.5/g=1.0/b=2.3 manual) → BayerDemosaic(RGBA16) →
GammaCorrection(1.2) → RGB8 変換 → SHM。RDMA/RoCE 不要(Linux ソケット受信)。

### Jetson AGX Thor での接続構成(動作実績あり)

```
Thor QSFPポート ─ 10Gtek QSA (QSFP+→SFP+) ─ ATGBICS SFP-10G-T-C (10GBase-T RJ45)
  ─ Cat8 ─ TRENDnet TPE-319GI PoE++インジェクタ [DATA|PoE] ─ Cat8 ─ VB1940
```

- Thor 側インターフェースは `mgbe0_0`(QSFP レーン0)。ホスト IP 192.168.0.101/24。
- PoE インジェクタは **10GBase-T 対応かつ 802.3bt** であること(1G 用インジェクタは
  給電できてもリンク訓練を通せない)。ケーブルは Cat6a 以上。

### 熱とリンクの癖(最重要・実測)

1. **カメラ本体は発熱で 10G リンクを落とす**。通電し続けたカメラは
   リンク確立後 数十秒〜数分で切断し、以後毎秒の PCS 再訓練ループに入る
   (`journalctl -k` に `PCS block lock SUCCESS` が毎秒出続けるのが特徴)。
   **ファン等での冷却が事実上必須**。冷却下では 30fps を安定維持できる。
2. **リンク断からの復帰はケーブル抜き差しでは(ほぼ)成功しない**。確実なのは
   mgbe 側の再初期化: `sudo ip link set mgbe0_0 down && sleep 2 && sudo ip link set mgbe0_0 up`
   → 1〜3 秒でリンク訓練が成功する(Jetson 再起動でも同効果)。
3. `hololink.reset()` はカメラ PHY を再起動させリンクを殺す(reference.md §4)。
4. 電源断でカメラ IP が消えるため、「リンクは上がるが ping 不通」は正常系。
   ワーカー(set-ip 内蔵)を起動すれば繋がる。
5. カメラの緑 LED は給電・起動時に点灯し、待機中は消灯していることがある。
   「LED 消灯 + 本体が温かい」= 給電は正常。

### 診断コマンド

```bash
cat /sys/class/net/mgbe0_0/carrier                      # 1=リンクあり
journalctl -k | grep "Link is"                           # リンク確立の履歴
journalctl -k --since '1 min ago' | grep -c "PCS block"  # 毎秒 ≈ 訓練失敗ループ
pkill -f "hololink set[-]ip"                             # リークしたデーモンの掃除
```

## 新しい HSB カメラ(IMX274 等)を対応させる手順

hsb_v2.5.0 は VB1940 の他に IMX274/IMX715/AR0234 など多数のセンサーを
`hololink.sensors.*` に持つ。別センサーの対応は:

1. **example の確認**: `examples/linux_<sensor>_player.py` が存在するか。
   存在すればそのセンサーは Linux ソケット受信(RDMA 不要)で動く。
2. **ワーカーの複製・修正**: `hsb_worker.py` のセンサー生成部
   (`Vb1940Cam` / `Vb1940_Mode`)と設定シーケンス(reset レジスタ・setup_clock の
   要否)を example に合わせて差し替える。モード表(解像度/fps)も更新。
3. **Config の追加**: 新しい `camera_mode` 表を `configuration_*.py` に定義。
   type 名は別にする(例: `hsb_imx274`)か、`sensor` フィールドで分岐する。
4. **enumeration の違いに注意**: IMX274 ステレオはポート2つ・IP2つ
   (192.168.0.2/0.3)を使う。VB1940 は単一 IP。DataChannel の作り方が変わる
   (`DataChannel.use_sensor(metadata, n)`)。
5. **実測データの追記**: 動作確認したら本ファイルにモード表・識別情報・
   癖(熱・リンク・タイミング)を追記する。

## 追記 (2026-08-13): 露光・ゲイン・縮小出力の実測知見

### 画像が暗い場合 — センサーレジスタで調整する

VB1940 のモード既定露光は短く、屋内では暗い(平均輝度 30/255 程度)。
プラグインの `exposure` / `analog_gain` 設定で改善する(configure() 後に
`set_exposure_reg` / `set_analog_gain_reg` を書く実装):

| 設定 | 平均輝度 (屋内実測) |
|---|---|
| 既定 | 30/255 |
| exposure=800, analog_gain=2 | 51/255 |
| **exposure=1000, analog_gain=6** | **81/255 (推奨)** |

- exposure は行数単位 (1行≈29.7µs)。30fps ではフレーム時間 33ms ≈ 1100 行が上限
- analog_gain は 0-12、倍率 = 16/(16-値)。ノイズが増えるため露光優先で調整
- **学習データと推論時で同一設定にすること**(輝度分布のずれは精度に直結)

### 縮小出力 (width/height) と解像度の設計

- `width`/`height` をモード解像度と変えるとワーカーが cv2 INTER_AREA で縮小出力する
  (例: camera_mode=1 + 640×360)。VB1940 の最小モードは 1080p なので、
  それ以下の解像度はこの機能でしか得られない
- 1080p は ACT 学習でチャンク推論 60ms・視覚トークン 2040 個と重い。
  **640×360 なら約 8 倍高速**(実測: 学習スループット 8.4 倍、推論も同等の短縮)
- 既存データセットの縮小変換は ffmpeg で可能 (フレーム数検証を必ず行う。
  同一コーデック設定 AV1/CRF30/GOP2 を維持し、meta/info.json の
  features.*.shape と video.height/width を書き換える)

### VLA ポリシーと組み合わせる場合の注意

- **SmolVLA** (`lerobot/smolvla_base`) はカメラ名 `camera1/2/3` 前提のため、
  学習・推論の両方に `--rename_map='{"observation.images.front": "observation.images.camera1"}'`
  が必要(データセット側がポリシーの部分集合なら検証は通る)
- **GR00T N1.7** は new_embodiment がデータセットのカメラ名に自動適応するため rename 不要
