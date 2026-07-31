# 対応ジョイスティック デバイス別リファレンス

プラグインは接続中のパッドを **USB vendor/product id で自動識別**し、機種ごとのプロファイル
(軸配置・レンジ・トリガー方式)を適用する。ここに各デバイスの実測特性を記録する。
汎用的な設計原則(自動識別・absinfo正規化・シード)は `reference.md` を参照。

---

## Logitech F710(プロファイル: `f710`)

| 項目 | 値 |
|---|---|
| USB id / デバイス名 | `046d:c21f` / "Logitech Gamepad F710"(XInputモード、kernel `xpad`)|
| スティック | 左 `ABS_X/ABS_Y`、右 `ABS_RX/ABS_RY` — **-32768..32767**、上=負 |
| トリガー | `ABS_Z`(LT)/ `ABS_RZ`(RT) — **アナログ 0..255** |
| 十字キー | `ABS_HAT0X/Y`(±1、上=-1)|
| ボタン | `BTN_A(304)/B(305)/X(307)/Y(308)`, `BTN_TL(310)/TR(311)`, `BTN_SELECT/START/MODE` |

注意点:
- **前面スイッチは「X」(XInput)で使う**。「D」(DirectInput)では別デバイス
  (`046d:c219` "Cordless RumblePad 2")になり、右スティックが `ABS_Z/ABS_RZ` に変わるなど
  配置が別物。プラグインはDモード検出時に「Xに切り替えて」と明示エラーを出す。
- スリープする。`/dev/input/event*` に現れない時は Logitech ボタンで起床。
- グリッパ操作: LT/RT のアナログ量(0..1)をそのまま開閉速度に使える。

## ELECOM JC-U3912T / JC-U3812T(プロファイル: `jc_u3912t`)

公式マニュアル: https://www.elecom.co.jp/support/manual/peripheral/gamepad/jc-u3912tbk/JC-U3912TBK_v1.pdf
(直接curlでは403。Web Archive経由なら取得可)

| 項目 | 値 |
|---|---|
| USB id / デバイス名 | `056e:200e` / **"Smart JC-U3912T"**(汎用HIDドライバ、DirectInput方式)|
| スティック | 左 `ABS_X/ABS_Y`、**右 `ABS_Z/ABS_RZ`(DirectInput配置)** — **0..255**、上=小、flat=15 |
| トリガー | **アナログ軸なし**(全ボタンデジタル)|
| 十字キー | `ABS_HAT0X/Y`(±1、上=-1)。POV(デジタル8方向)|
| ボタン | 印字番号①〜⑫が evdev 304〜315 に**線形対応**: ①=BTN_A(304) ②=BTN_B(305) ③=BTN_C(306) ④=BTN_X(307) ⑤=BTN_Y(308) ⑥=BTN_Z(309) ⑦=BTN_TL(310) ⑧=BTN_TR(311) **⑨=BTN_TL2(312)=左スティック押し込み** **⑩=BTN_TR2(313)=右スティック押し込み** ⑪=BTN_SELECT(314) ⑫=BTN_START(315) |

物理配置(マニュアルより): ①〜④=前面右のボタン群、⑤〜⑧=ショルダー4ボタン、
**⑨⑩=スティック押し込み**、⑪⑫=前面中央(SELECT/START位置)。

プロファイルの割当: グリッパ開=⑦ / 閉=⑧(ショルダー)、
エピソードイベント: 成功=① / 失敗=② / 再記録=③ / 介入(押しっぱなし)=⑥。

注意点:
- **スティックのレンジが 0..255(中央127.5)** — ±32767前提の正規化では全く動かない/誤動作する。
  absinfo の min/max から `(raw − center)/half` で正規化すること。
- **absinfo の初期 value が 0 のまま返る**(実際はスティック中央でも)。接続直後に
  absinfo.value をそのまま信じると「左上全開」の誤入力になる。**スティック/ハットは
  センター値、アナログトリガーは min でシード**し、最初のイベントから実値を使う。
- **省電力モードの罠**: 電源ON状態で約10分無操作で自動スリープ。**復帰できるのは
  AUTO・十字キー・①〜④・⑦〜⑫のみ — スティックと⑤⑥では復帰しない**。
  「スティックだけ無反応」に見えたらまず十字キーか①ボタンを押して起こすこと
  (復帰後2〜3秒は動作が不安定な場合あり、マニュアル記載)。
- アナログ/デジタルのモード切替ボタンは**存在しない**(常時アナログモード)。
- グリッパ操作はデジタル(0/1)なので開閉は一定速度になる。
- AUTOボタンは連射機能の設定用(AUTO+対象ボタンで連射モード切替)。誤って押すと
  ボタンが連射化されるので注意。もう一度 AUTO+対象ボタンで解除。

---

## 新しいパッドを対応させる手順

1. パッドを接続してケイパビリティを収集する:

```bash
python3 -c "
import evdev
from evdev import ecodes
for p in evdev.list_devices():
    d = evdev.InputDevice(p)
    print(p, d.name, f'{d.info.vendor:04x}:{d.info.product:04x}')
    for code, info in d.capabilities().get(ecodes.EV_ABS, []):
        print('  ABS', ecodes.ABS[code], info)
    print('  KEYS', d.capabilities().get(ecodes.EV_KEY, []))"
```

2. プロファイルを定義する(実装例: `lerobot_teleoperator_f710/f710_input.py` の `GamepadProfile`):
   - `vendor` / `product`: 手順1のUSB id
   - `axes`: 6チャンネル(`left_x/left_y/right_x/right_y/dpad_x/dpad_y`)→ ABSコード
   - `trigger_axes`(アナログトリガーがある場合)または `trigger_buttons`(デジタルの場合)
3. `PROFILES` 辞書に登録すれば自動識別の対象になる。
4. 入力モニタCLI(`lerobot-f710-monitor`)で全チャンネル・ボタンの動作を目視確認してから
   ロボットに接続する。
5. 実測特性(レンジ、配置、癖)を本ファイルに追記する。
