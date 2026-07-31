# 対応SpaceMouse デバイス別リファレンス

プラグインは接続中のデバイスを **USB vendor/product id で自動識別**する。
3Dconnexionのvendor idは `256f`(2010年代以降。旧機種はLogitechの `046d` を借用)。

---

## SpaceMouse Compact(プロファイル: `spacemouse_compact`)【実機検証済み 2026-07-31】

| 項目 | 値 |
|---|---|
| USB id / デバイス名 | `256f:c635` / "3Dconnexion SpaceMouse Compact"(hid-generic) |
| 軸 | EV_REL: `REL_X`(左右スライド) `REL_Y`(前後スライド) `REL_Z`(押し込み、下=正) `REL_RX`(前後チルト) `REL_RY`(左右チルト) `REL_RZ`(ツイスト) |
| 値 | **現在の変位 ±350** を操作中約60Hzでストリーム。**リリース時のゼロイベントなし**(カーネルが値0のRELを破棄) |
| ボタン | `BTN_0`(256、左)/ `BTN_1`(257、右)のみ |
| その他 | EV_LED×1。hidraw(hidraw*)としても見えるがevdevで十分 |

プラグインでの割当: twist→shoulder_pan、push→shoulder_lift(符号反転)、
slide_y→elbow_flex、tilt_fwd→wrist_flex、tilt_side→wrist_roll、slide_x=予備、
左ボタン=グリッパ開、右ボタン=閉。

注意点:
- **リリース検出はホールドタイムアウト必須**(reference.md §1)。
- スライド操作にチルトが混入しやすい(デッドゾーン0.1以上を推奨)。
- 権限: udevルール(vendor 256f)推奨。無いと evdev から見えない。

---

## 新しいSpaceMouseを対応させる手順

1. `lsusb | grep -i 3dconn` でproduct idを確認し、evdevノードを特定
   (`/proc/bus/input/devices` の Handlers)。
2. タイムライン付きキャプチャ(joystick編の手順を流用)で軸コード・レンジ・
   ストリーミングレート・**リリース時のゼロイベント有無**を実測。
3. `SpaceMouseProfile` を定義(vendor/products、axes、gripper_buttons)し
   `PROFILES` に登録。ボタンが4個以上ある機種(SpaceMouse Pro等)は
   エピソードイベント割当も追加できる。
4. `lerobot-spacemouse-monitor` で全チャンネル確認 → リリーステスト → 実測を本ファイルに追記。
