# LeRobot 0.6.0 MIDIテレオペレータープラグイン実装知見

SMC-Mixer + SO-101 フォロワー + Jetson での実機構築(2026-07-31)で得た知見。
実装の実例: `lerobot_teleoperator_midi`(ALSA raw MIDI直読み、pytest 20件、実機検証済み)。
プラグイン規約・座標系・キャリブレーション・サーボゲイン等の共通知見は
`../../joystick-teleop-skills/reference/reference.md` を参照(重複記載しない)。

---

## 1. 絶対位置制御(フェーダー)の設計

- フェーダー位置を関節可動域に線形マップする: `target = min + norm × (max − min)`。
  可動域はロボットのキャリブレーションJSONから導出(度数モード、joystick編§3と同じ式)。
  **フェーダーのフルストローク=関節の全可動域**になるので直感的。
- **ソフトテイクオーバー(ピックアップ)が必須**:
  - rawMIDIではフェーダーの現在物理位置を照会できない(動かして初めて値が届く)。
  - 未エンゲージのフェーダーはターゲットに影響させず、
    (1) 値が現在目標のフェーダー換算値とε以内、または
    (2) 前回値と今回値が目標を挟んだ(符号テスト `(prev−equiv)×(now−equiv) ≤ 0`)
    ときにエンゲージして以後追従。
  - 横切り判定がないと高速スイープでピックアップ点を飛び越えて掴めない。
- 「一度も動いていない = None」を型で明示し、None の間は initial_position を保持。
- ジョイスティックと違い速度パラメータは不要(操作速度=フェーダーを動かす速さ)。
  急激な入力への保険としてロボット側 `--robot.max_relative_target=5` の併用を推奨。

## 2. ALSA raw MIDI 直読み(依存ゼロ)

- `/dev/snd/midiC<card>D<dev>` を `os.open(O_RDONLY|O_NONBLOCK)` で開き、
  `os.read` ループ + 自前パーサで十分(mido/python-rtmidi不要)。
- デバイス発見: `/dev/snd/midiC*D*` を走査し、`/proc/asound/card<N>/id` と `longname` を
  プロファイルの正規表現と照合して自動識別。
- **サブデバイス選択**: `/dev/snd/midiCxDy` の素のopenはサブデバイス0固定。
  他サブデバイス(例: SMC-MixerのMaster=1)を開くには、`/dev/snd/controlC<card>` に
  `SNDRV_CTL_IOCTL_RAWMIDI_PREFER_SUBDEVICE`(**`_IOW('U',0x42,int)` = 0x40045542**)を
  発行してから midi ノードを開く(**ctlハンドルはmidiのopenが終わるまで開いたまま**にする。
  カーネルは同一PIDのctlファイルを参照する)。alsa-libと同じ機構で、依存なしで実現できる。
  **要注意: `'U',0x32`(0x40045532)はPCM用の別ioctl** — 間違えてもエラーにならず
  サブデバイス0が静かに開く(実際にやらかした)。開いたfdに
  `SNDRV_RAWMIDI_IOCTL_INFO`(0x810C5701、構造体268バイト)を発行して実サブデバイス番号・
  サブ名を検証すること(検証コードがこのバグを検出した)。INFOの罠: 構造体の `stream`
  フィールド(オフセット8)に **1(=INPUT)をセットしてから** 発行する。ゼロのまま
  (=OUTPUT)だと O_RDONLY で開いたfdには出力側が無いため ENODEV になる。
  ioctl定数はユニットテストで固定しておく(1ニブル違いが無音で挙動を変えるため)。
- MIDIパーサの要点:
  - **ランニングステータス**: ステータスバイト省略の連続データに対応。
  - **リアルタイムバイト(0xF8〜0xFF)はメッセージ途中に割り込む** — 無視しつつ
    ランニングステータスを壊さない。
  - システムコモン(0xF0〜0xF7)はランニングステータスを解除。
  - Note On velocity 0 = Note Off(SMC-Mixerもこの形式)。
  - Pitch Bend は 14bit: `value = data0 | (data1 << 7)`。

## 3. キャプチャ(プロトコル実測)の落とし穴

- `timeout N amidi -p hw:x,y,z -d > file` は **SIGTERM時にstdioバッファが
  フラッシュされず0バイトになる**。`stdbuf -oL amidi ...` にするか、自作リーダーで
  15秒毎スナップショット保存にする(実際に一度全損した)。
- 複数ポートを持つデバイスは**全ポート同時キャプチャ**で搬送内容を比較する。
  SMC-Mixerでは**Privateポートはフェーダー(PitchBend)のミラーのみで、ボタン(Note)は
  流れない**(実測)。Masterが完全。誤ったポートを開くとフェーダーは動くのに
  ボタンだけ死ぬ、という発見しにくい故障になる — ポート選択の実行時検証が重要な理由。
- ユーザー操作の指示は「1コントロールずつ、フルストローク」。コントロールは
  識別子(CC番号/チャンネル)で区別できるので操作順は厳密でなくてよい。

## 4. Mackie Control(MCU)系デバイスの特徴

- フェーダー = **Pitch Bend チャンネル別**(ch0〜7 = フェーダー1〜8)。値は
  実質 MSB×128(0..16256)。CCではない点に注意。
- ロータリーノブ = **相対値CC**(cc16〜23、+1=0x01 / −1=0x41 のサイン・マグニチュード)。
  絶対位置が無いのでフェーダーの代わりには使いにくい(将来は速度調整等に活用可)。
- トランスポートボタン = Note(91=◀◀ 92=▶▶ 93=■ 94=▶ 95=● 46/47=バンク切替)。
- モーターフェーダー非搭載機(SMC-Mixer)は、ホストから位置を書き戻せない。
  テイクオーバー方式が唯一の整合手段。

## 5. 実機検証の記録(2026-07-31)

- SMC-Mixer(ALSAカード "SINCO"、Jieli Technology)を自動識別、Masterサブデバイス接続
  (INFO ioctl による実行時ポート検証付き: `(1, 'SINCO SMC-Mixer-Master')`)。
- 90秒ドライラン: 全6関節がフェーダー順にピックアップされ、キャリブレーション由来の
  可動域いっぱい(pan ±120.4° / lift・elbow・roll ±180° / wrist_flex ±106.4° /
  gripper 0..100)まで追従。ソフトテイクオーバーの誤エンゲージなし。
- ボタンイベント検証: ▶=成功 / ●=再記録 / ■=失敗(終了) / ◀◀長押し=介入 の
  4種すべて get_teleop_events 経由で検出(Masterポート必須 — Privateにはボタンが流れない)。
- BTボタンの点灯はBluetooth MIDIのインジケータ。USB利用では無害。

## 6. LeRobotイベント契約の注意

- `get_teleop_events` の `TERMINATE_EPISODE` は **FAILURE / RERECORD のときのみ True**
  にする(組み込みgamepadテレオペと同じ契約)。SUCCESS単独で終了フラグを立てると、
  `hil_processor` の `terminate_on_success=False` 設定を無視する挙動になる。
- ボタンのNote On/Off が同一poll内に収まる高速タップ(33ms未満)は、set→clear が
  1回の更新内で相殺されイベントを取りこぼしうる(理論上の穴、実用上未発生)。
