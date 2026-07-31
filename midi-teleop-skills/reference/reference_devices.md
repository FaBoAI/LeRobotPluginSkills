# 対応MIDIコントローラ デバイス別リファレンス

プラグインは接続中のコントローラを **ALSAカード名の正規表現マッチで自動識別**する。
ここに各デバイスの実測プロトコルを記録する。キャプチャ手順は SKILL.md Step 2 と
`reference.md` §3 を参照。

---

## SMC-Mixer(プロファイル: `smc_mixer`)【実機検証済み 2026-07-31】

| 項目 | 値 |
|---|---|
| ALSA識別 | カードid `SINCO`(Jieli Technology)。rawMIDIポート名 `SINCO SMC-Mixer-Private` / `SINCO SMC-Mixer-Master` |
| ポート構成 | サブデバイス0=Private、**サブデバイス1=Master(こちらが完全なデータを搬送。Privateはミラーで一部欠落)** |
| プロトコル | Mackie Control(MCU)方式 |
| フェーダー1〜8 | **Pitch Bend ch0〜7**。値 0..16256(LSB=0、実質MSB×128) |
| ノブ1〜8 | **相対値CC ch0 cc16〜23**(+1=`0x01`、−1=`0x41`) |
| トランスポート | Note ch0: 91=◀◀、92=▶▶、93=■停止、94=▶再生、95=●録音(velocity 127/0) |
| バンク切替 | Note ch0: 46=◀、47=▶ |
| その他 | モーターフェーダーなし(ホストから位置の書き戻し不可)。USBオーディオ機能も同居(カード名SINCO)。**BTボタンの点灯はBluetooth MIDIインジケータ — USB利用時は無視してよい** |

プラグインでの割当: フェーダー1〜6 → shoulder_pan / shoulder_lift / elbow_flex /
wrist_flex / wrist_roll / gripper(絶対位置)、▶=成功、■=失敗、●=再記録、◀◀長押し=介入。
フェーダー7/8とノブは予備。

---

## 新しいMIDIコントローラを対応させる手順

1. `amidi -l` で全ポートを確認し、**全ポート同時に**キャプチャする
   (`stdbuf -oL amidi -p hw:X,Y,Z -d` または自作リーダー。SKILL.md Step 2 の
   バッファリングの罠に注意)。
2. ユーザーに1コントロールずつフルストローク操作してもらい、解析する:
   - フェーダー: CC(7bit)か Pitch Bend(14bit・チャンネル別)か
   - ノブ: 絶対値か相対値(+1/−1エンコーディングの形式)か
   - ボタン: Note番号、velocity、トグルかモーメンタリか
3. `MidiProfile` を定義(`card_pattern`(ALSAカード名の正規表現)、`subdevice`、
   `joint_faders`(FaderSpec: `("pb", ch)` または `("cc", ch, cc)`)、`event_buttons`)し、
   `PROFILES` に登録。
4. `lerobot-midi-monitor` で全コントロールの認識を目視確認してからロボットに接続。
5. 実測プロトコルを本ファイルに追記する。
