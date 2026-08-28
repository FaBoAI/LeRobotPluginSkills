#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from lerobot.teleoperators.config import TeleoperatorConfig


@TeleoperatorConfig.register_subclass("otter_leader")
@dataclass
class OtterLeaderConfig(TeleoperatorConfig):
    # USB シリアルポート (例: "/dev/ttyUSB0")
    port: str

    # どちら側のコントローラを有効にするか
    use_right: bool = True
    use_left: bool = True

    # Dynamixel の型番
    motor_model: str = "xl330-m288"

    # 各 Leader の ID 開始番号とサーボ数。
    # start_id は最小 ID として扱い、id_order により昇順/降順を切り替えます。
    # デフォルトは、7DOF + Gripper に合わせて
    #   LEFT : ID 1,2,3,4,5,6,7,8
    #   RIGHT: ID 11,12,13,14,15,16,17,18
    # の順で motor_names に割り当てます。
    left_start_id: int = 1
    left_motor_count: int = 8
    left_id_order: str = "ascending"

    right_start_id: int = 11
    right_motor_count: int = 8
    right_id_order: str = "ascending"

    # 連番ではない ID を使う場合、または ID 順を完全に指定したい場合は、
    # 設定ファイルで明示的に指定できます。
    # 空リストの場合は *_start_id / *_motor_count / *_id_order から自動生成します。
    # 例: left_motor_ids: [1, 2, 3, 4, 5, 6, 7, 8]
    left_motor_ids: List[int] = field(default_factory=list)
    right_motor_ids: List[int] = field(default_factory=list)

    # モータ名を変更したい場合に指定します。
    # 空リストの場合、7DOF + Gripper の以下 8 軸名を自動生成します。
    #   shoulder_yaw, shoulder_pitch, shoulder_roll, elbow_pitch,
    #   wrist_yaw, wrist_pitch, wrist_roll, gripper
    # side 別の *_motor_names が指定された場合は、それを優先します。
    motor_names: List[str] = field(default_factory=list)
    left_motor_names: List[str] = field(default_factory=list)
    right_motor_names: List[str] = field(default_factory=list)

    # gripper として扱うモータ名。
    # ここに含まれるモータは RANGE_0_100 / CURRENT_POSITION として扱います。
    gripper_motor_names: List[str] = field(default_factory=lambda: ["gripper"])

    # Drive_Mode を反転する関節名。
    # side prefix を除いた base 名で指定します。例: left_elbow_pitch -> elbow_pitch
    inverted_motor_names: List[str] = field(default_factory=lambda: ["elbow_pitch"])

    # Gripper の「オープン」位置 (current-position mode 用の raw 値)
    gripper_open_pos: float = 50.0

    # ---- ドリフト対策 (弱電流の位置保持) ----
    # 重力でドリフトする関節 (特に shoulder_roll = 上腕ねじりの冗長軸) を、
    # 電流制限付き位置制御 (XL330 Operating Mode 5) + 弱い Goal_Current で
    # 「弱いバネ」のように接続時の姿勢へ保持する。オペレーターは普通に動かせ、
    # 読み取り値 (Present_Position) もそのまま使える。左右両方に適用される。
    # side prefix を除いた base 名で指定。空リストで無効。
    drift_hold_motor_names: List[str] = field(
        default_factory=lambda: ["shoulder_yaw", "shoulder_pitch", "shoulder_roll"]
    )
    # 保持電流 [mA] (XL330 の Goal_Current 単位 = 1mA)。15〜60mA で調整。
    # 大きいほど強く戻るが、操作が重くなる。0 で無効。
    # 実機フィードバックで 40→25 に調整 (2026-08-16「もう少し弱く」)
    drift_hold_current_ma: int = 25
    # 関節別の保持電流上書き (base 名 → mA)。未指定の関節は drift_hold_current_ma。
    # 実機フィードバックで shoulder_roll のみ 25→18 に緩和 (2026-08-28)
    drift_hold_current_overrides: Dict[str, int] = field(
        default_factory=lambda: {"shoulder_roll": 18}
    )

    # ---- グリッパ読み値の反転 ----
    # 再キャリブレーションやサーボ整備でグリッパの正規化方向が
    # データセット収録時と逆転した場合に、読み値を 100-v に反転する。
    # フォロワー側の open/close を変えると過去のデータセット・学習済み
    # ポリシーと非互換になるため、必ずリーダー側 (ここ) で合わせること。
    # full 名 (left_gripper / right_gripper) で指定。
    flipped_gripper_names: List[str] = field(default_factory=list)

    # 元実装互換。今回は特に使わないが、config schema のために残す。
    drive_joint_name: str = "wrist_roll"
