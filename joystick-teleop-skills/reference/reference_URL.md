# LeRobot サードパーティプラグイン関連リファレンス URL

## 公式ドキュメント

- プラグイン統合ガイド(4つの規約: パッケージ名プレフィックス / Config命名 / モジュール配置 / __init__ 公開)
  https://huggingface.co/docs/lerobot/integrate_hardware
  (lerobotリポジトリ同梱版: `docs/source/integrate_hardware.mdx` — 「Using Your Own LeRobot Devices」節)
- SO-101 組み立て・キャリブレーション手順(中央姿勢の動画あり)
  https://huggingface.co/docs/lerobot/so101
- LeRobot 本体
  https://github.com/huggingface/lerobot

## プラグイン検出・デバイス解決の実装(LeRobot 0.6.0 ソース)

- `lerobot/utils/import_utils.py` — `register_third_party_plugins()`(`lerobot_teleoperator_` 等の
  プレフィックス走査)と `make_device_from_device_class()`(Config→デバイスクラス解決)
- `lerobot/teleoperators/teleoperator.py` — `Teleoperator` 抽象基底クラス(実装必須メンバー)
- `lerobot/teleoperators/so_leader/` — ジョイント空間テレオペのリファレンス実装
- `lerobot/motors/motors_bus.py` — `_normalize` / `_unnormalize`
  (**DEGREES モードはクランプなし**、RANGE_M100_100 は ±100 クランプ)
- `lerobot/robots/so_follower/config_so_follower.py` — **`use_degrees: True` がデフォルト**

## コミュニティのプラグイン実例(公式ドキュメント掲載)

- https://github.com/SpesRobotics/lerobot-robot-xarm
- https://github.com/SpesRobotics/lerobot-teleoperator-teleop

## 本スキルでの実装例

- `lerobot_teleoperator_f710` — Logitech F710(evdev、Jetson ヘッドレス対応、
  度数モード対応、キャリブレーション由来の関節リミット自動導出、
  `lerobot-f710-monitor` / `lerobot-f710-check-calib` 同梱)
  ※ 実装知見は同ディレクトリの `reference.md` を参照

## デバイス関連

対応デバイスの実測特性(軸配置・レンジ・癖)は `reference_devices.md` に集約。

- python-evdev ドキュメント: https://python-evdev.readthedocs.io/
- Logitech F710: XInput モード = USB `046d:c21f`(kernel `xpad`)、
  DirectInput モード = `046d:c219`("Cordless RumblePad 2"、軸配置が別物)
- ELECOM JC-U3912T/JC-U3812T: USB `056e:200e`("Smart JC-U3912T"、汎用HID。
  スティック0..255、右スティック ABS_Z/RZ、トリガーはデジタルボタン)
  - 公式マニュアル(ボタン番号①〜⑫の物理配置・省電力モード・連射機能):
    https://www.elecom.co.jp/support/manual/peripheral/gamepad/jc-u3912tbk/JC-U3912TBK_v1.pdf
- Feetech STS3215(SO-101 のサーボ): 12bit(0..4095)アブソリュートエンコーダ、
  シングルターン位置制御。境界 0/4095 をまたぐ位置指令ジャンプは「ほぼ1回転の誤差」として
  長い方向へ巻き戻るため、指令側でジャンプを発生させないこと
