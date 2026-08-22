#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import termios
import threading
import time
from typing import Dict, List

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.dynamixel import (
    DriveMode,
    DynamixelMotorsBus,
    OperatingMode,
)
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from lerobot.teleoperators.teleoperator import Teleoperator
from .config_otter_leader import OtterLeaderConfig

logger = logging.getLogger(__name__)

_DEFAULT_BASE_MOTOR_NAMES = (
    "shoulder_yaw",
    "shoulder_pitch",
    "shoulder_roll",
    "elbow_pitch",
    "wrist_yaw",
    "wrist_pitch",
    "wrist_roll",
    "gripper",
)


class OtterLeader(Teleoperator):
    """
    OtterLeader テレオペレータ (left / right 両腕対応)。

    デフォルトの Dynamixel ID 割り当て:

        left 側:
            *  1 = left_shoulder_yaw
            *  2 = left_shoulder_pitch
            *  3 = left_shoulder_roll
            *  4 = left_elbow_pitch
            *  5 = left_wrist_yaw
            *  6 = left_wrist_pitch
            *  7 = left_wrist_roll
            *  8 = left_gripper

        right 側:
            * 11 = right_shoulder_yaw
            * 12 = right_shoulder_pitch
            * 13 = right_shoulder_roll
            * 14 = right_elbow_pitch
            * 15 = right_wrist_yaw
            * 16 = right_wrist_pitch
            * 17 = right_wrist_roll
            * 18 = right_gripper

    ID 範囲、ID 順、サーボ数、モータ名は OtterLeaderConfig から変更できます。

    get_action():
        - bus.sync_read("Present_Position") が返す正規化値を使用
        - gripper_motor_names に含まれるモータ: 0〜100 のまま
        - その他の関節: -100〜100 を 0〜100 にシフト
    """

    config_class = OtterLeaderConfig
    name = "otter_leader"

    def __init__(self, config: OtterLeaderConfig):
        super().__init__(config)
        self.config = config
        self._lock = threading.Lock()

        self._gripper_base_names = set(self.config.gripper_motor_names)
        self._inverted_base_names = set(self.config.inverted_motor_names)
        self._side_motor_ids: Dict[str, List[int]] = {}
        self._side_motor_names: Dict[str, List[str]] = {}

        self.bus = DynamixelMotorsBus(
            port=self.config.port,
            motors=self._build_motors(),
            calibration=self.calibration,
        )

    # ------------------------------------------------------------------ #
    # モータ定義の生成
    # ------------------------------------------------------------------ #
    @staticmethod
    def _base_motor_name(motor_name: str) -> str:
        """left_ / right_ prefix を除いた base 名を返す。"""
        for side in ("left", "right"):
            prefix = f"{side}_"
            if motor_name.startswith(prefix):
                return motor_name[len(prefix):]
        return motor_name

    @staticmethod
    def _default_motor_names(count: int) -> List[str]:
        """サーボ数に応じたデフォルトの base モータ名を返す。

        標準は 7DOF + Gripper の 8 軸。
        サーボ数を 8 より少なくした場合も、2軸以上であれば gripper は最後に残す。
        8軸を超える場合は gripper の手前に joint_N を追加する。
        """
        if count <= 0:
            raise ValueError("motor_count must be greater than 0")

        if count == 1:
            return [list(_DEFAULT_BASE_MOTOR_NAMES)[0]]

        if count <= len(_DEFAULT_BASE_MOTOR_NAMES):
            return list(_DEFAULT_BASE_MOTOR_NAMES[: count - 1]) + ["gripper"]

        extra_count = count - len(_DEFAULT_BASE_MOTOR_NAMES)
        extra_names = [f"joint_{i}" for i in range(8, 8 + extra_count)]
        return list(_DEFAULT_BASE_MOTOR_NAMES[:-1]) + extra_names + ["gripper"]

    @staticmethod
    def _extend_motor_names(names: List[str], count: int) -> List[str]:
        """設定ファイルで指定されたモータ名が count に満たない場合に補完する。"""
        expanded = list(names[:count])
        next_index = 1
        while len(expanded) < count:
            candidate = f"joint_{next_index}"
            next_index += 1
            if candidate not in expanded:
                expanded.append(candidate)
        return expanded

    def _ids_for_side(self, side: str) -> List[int]:
        explicit_ids = list(getattr(self.config, f"{side}_motor_ids"))
        if explicit_ids:
            ids = explicit_ids
        else:
            start_id = int(getattr(self.config, f"{side}_start_id"))
            count = int(getattr(self.config, f"{side}_motor_count"))
            if count <= 0:
                raise ValueError(f"{side}_motor_count must be greater than 0")

            ids = list(range(start_id, start_id + count))
            id_order = str(getattr(self.config, f"{side}_id_order", "ascending")).lower()
            if id_order in ("descending", "desc", "reverse", "reversed"):
                ids.reverse()
            elif id_order in ("ascending", "asc", "normal"):
                pass
            else:
                raise ValueError(
                    f"{side}_id_order must be 'ascending' or 'descending', got: {id_order!r}"
                )

        if len(set(ids)) != len(ids):
            raise ValueError(f"{side}_motor_ids contains duplicate IDs: {ids}")
        return ids

    def _names_for_side(self, side: str, count: int) -> List[str]:
        side_names = list(getattr(self.config, f"{side}_motor_names"))
        if side_names:
            names = self._extend_motor_names(side_names, count)
        elif self.config.motor_names:
            names = self._extend_motor_names(list(self.config.motor_names), count)
        else:
            names = self._default_motor_names(count)

        if len(set(names)) != len(names):
            raise ValueError(f"{side}_motor_names contains duplicate names: {names}")
        return names

    def _norm_mode_for_base_name(self, base_name: str) -> MotorNormMode:
        if base_name in self._gripper_base_names:
            return MotorNormMode.RANGE_0_100
        return MotorNormMode.RANGE_M100_100

    def _build_motors(self) -> Dict[str, Motor]:
        motors: Dict[str, Motor] = {}
        all_ids: Dict[int, str] = {}

        for side in ("left", "right"):
            if not getattr(self.config, f"use_{side}"):
                continue

            ids = self._ids_for_side(side)
            names = self._names_for_side(side, len(ids))
            self._side_motor_ids[side] = ids
            self._side_motor_names[side] = names

            for motor_id, base_name in zip(ids, names):
                motor_name = f"{side}_{base_name}"
                if motor_id in all_ids:
                    raise ValueError(
                        f"Dynamixel ID {motor_id} is used by both {all_ids[motor_id]} and {motor_name}"
                    )
                all_ids[motor_id] = motor_name
                motors[motor_name] = Motor(
                    motor_id,
                    self.config.motor_model,
                    self._norm_mode_for_base_name(base_name),
                )

        if not motors:
            raise ValueError("At least one of use_left or use_right must be True")

        logger.info("OtterLeader motor map: %s", {name: motor.id for name, motor in motors.items()})
        return motors

    def _is_gripper_motor(self, motor_name: str) -> bool:
        return self._base_motor_name(motor_name) in self._gripper_base_names

    def _is_drift_hold_motor(self, motor_name: str) -> bool:
        if self.config.drift_hold_current_ma <= 0:
            return False
        return self._base_motor_name(motor_name) in set(self.config.drift_hold_motor_names)

    def _is_inverted_motor(self, motor_name: str) -> bool:
        return self._base_motor_name(motor_name) in self._inverted_base_names

    def _id_summary(self) -> str:
        summaries: List[str] = []
        for side in ("left", "right"):
            ids = self._side_motor_ids.get(side)
            if not ids:
                continue
            if ids == list(range(ids[0], ids[0] + len(ids))):
                summaries.append(f"{side}: IDs {ids[0]}–{ids[-1]} ({len(ids)} motors)")
            elif ids == list(range(ids[0], ids[-1] - 1, -1)):
                summaries.append(f"{side}: IDs {ids[0]}–{ids[-1]} descending ({len(ids)} motors)")
            else:
                summaries.append(f"{side}: IDs {ids} ({len(ids)} motors)")
        return ", ".join(summaries)

    # ------------------------------------------------------------------ #
    # Teleoperator 抽象メソッド
    # ------------------------------------------------------------------ #
    @property
    def action_features(self) -> Dict[str, type]:
        # 全モータの "<name>.pos" を action key として出す
        # 例: "left_shoulder_yaw.pos", "right_wrist_roll.pos"
        return {f"{motor}.pos": float for motor in self.bus.motors}

    @property
    def feedback_features(self) -> Dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected

    # ------------------------------------------------------------------ #
    # 接続・キャリブ・設定
    # ------------------------------------------------------------------ #
    def connect(self, calibrate: bool = True) -> None:
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{self} already connected")

        # USB シリアル (FTDI) は EMI 等で瞬断することがあるが、十数秒で
        # 自動再接続される (error -71 → 再列挙)。1回だけ待って再試行する。
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._connect_once(calibrate)
                logger.info("%s connected.", self)
                return
            except (OSError, termios.error) as e:
                last_error = e
                logger.warning(
                    "OtterLeader: シリアル通信エラー (%s)。USB の再接続を待って再試行します (%d/2)",
                    e, attempt + 1,
                )
                try:
                    self.bus.disconnect()
                except Exception:
                    pass
                if not self._wait_for_port(timeout_s=20.0):
                    break
                time.sleep(1.0)  # 再列挙直後の安定待ち
        raise DeviceNotConnectedError(
            f"{self}: シリアルポート {self.config.port} に接続できません "
            f"(USB ケーブル/ハブを確認。直近のエラー: {last_error})"
        )

    def _connect_once(self, calibrate: bool) -> None:
        self.bus.connect()
        if not self.is_calibrated and calibrate:
            logger.info(
                "Mismatch between calibration values in the motor and the calibration file or no calibration file found"
            )
            self.calibrate()
        self.configure()

    def _wait_for_port(self, timeout_s: float) -> bool:
        """USB 再列挙でポートが戻るのを待つ。"""
        import os

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if os.path.exists(self.config.port):
                return True
            time.sleep(0.5)
        logger.error("OtterLeader: %s が %.0f 秒以内に復帰しませんでした", self.config.port, timeout_s)
        return False

    @property
    def is_calibrated(self) -> bool:
        return self.bus.is_calibrated

    def calibrate(self) -> None:
        """
        有効化されている全 Leader / 全関節をキャリブレーションする。

        1. 全関節を「真ん中」にして half-turn homing を実施
        2. すべての関節について range_min / range_max を実際に動かして計測
        """
        self.bus.disable_torque()

        if self.calibration:
            user_input = input(
                f"Press ENTER to use provided calibration file associated with the id {self.id}, "
                f"or type 'c' and press ENTER to run calibration: "
            )
            if user_input.strip().lower() != "c":
                logger.info(
                    "Writing calibration file associated with the id %s to the motors",
                    self.id,
                )
                self.bus.write_calibration(self.calibration)
                return

        logger.info("\nRunning calibration of %s (%s)", self, self._id_summary())

        # キャリブレーション中は gripper を含め、全モータを EXTENDED_POSITION にする
        for motor in self.bus.motors:
            self.bus.write("Operating_Mode", motor, OperatingMode.EXTENDED_POSITION.value)

        # 設定された関節の回転方向を反転
        inverted_motors = [m for m in self.bus.motors if self._is_inverted_motor(m)]
        for motor in inverted_motors:
            self.bus.write("Drive_Mode", motor, DriveMode.INVERTED.value)

        drive_modes = {
            motor: 1 if motor in inverted_motors else 0
            for motor in self.bus.motors
        }

        # 真ん中に合わせて half-turn homing
        input(
            "Move all enabled leader arms to the middle of their range of motion "
            "and press ENTER...."
        )
        homing_offsets = self.bus.set_half_turn_homings()

        # すべての joint を range 測定対象にする
        unknown_range_motors = list(self.bus.motors)

        print(
            "Move all joints sequentially through their entire ranges of motion.\n"
            "Recording positions for: "
            + ", ".join(unknown_range_motors)
            + "\nPress ENTER to stop..."
        )
        range_mins, range_maxes = self.bus.record_ranges_of_motion(unknown_range_motors)

        # range_min / range_max を MotorCalibration に書き込む
        self.calibration = {}
        for name, motor in self.bus.motors.items():
            self.calibration[name] = MotorCalibration(
                id=motor.id,
                drive_mode=drive_modes[name],
                homing_offset=homing_offsets[name],
                range_min=range_mins[name],
                range_max=range_maxes[name],
            )

        self.bus.write_calibration(self.calibration)
        self._save_calibration()
        logger.info("Calibration saved to %s", self.calibration_fpath)

    def configure(self) -> None:
        self.bus.disable_torque()
        self.bus.configure_motors()

        # gripper・ドリフト保持関節以外は EXTENDED_POSITION (トルクフリー)
        for motor in self.bus.motors:
            if self._is_gripper_motor(motor) or self._is_drift_hold_motor(motor):
                continue
            self.bus.write("Operating_Mode", motor, OperatingMode.EXTENDED_POSITION.value)

        # gripper は CURRENT_POSITION にしてオープン位置へ
        for motor in self.bus.motors:
            if not self._is_gripper_motor(motor):
                continue
            self.bus.write("Operating_Mode", motor, OperatingMode.CURRENT_POSITION.value)
            self.bus.enable_torque(motor)
            if self.is_calibrated:
                self.bus.write("Goal_Position", motor, self.config.gripper_open_pos)

        # ドリフト対策: 対象関節を CURRENT_POSITION (電流制限付き位置制御) + 弱電流で
        # 接続時の姿勢に保持する。重力ドリフト (特に shoulder_roll = 上腕ねじりの
        # 冗長軸が「広がる側」へ落ちる) を弱いバネとして受け止め、操作は妨げない。
        # 位置保持は両方向対称なので、左右で重力方向の符号が逆でも同じ設定でよい。
        for motor in self.bus.motors:
            if not self._is_drift_hold_motor(motor) or self._is_gripper_motor(motor):
                continue
            self.bus.write("Operating_Mode", motor, OperatingMode.CURRENT_POSITION.value)
            self.bus.write("Goal_Current", motor, int(self.config.drift_hold_current_ma))
            self.bus.enable_torque(motor)
            if self.is_calibrated:
                pos = float(self.bus.read("Present_Position", motor))
                self.bus.write("Goal_Position", motor, pos)
                logger.info(
                    "OtterLeader drift-hold: %s を %dmA で現在位置 %.1f に弱保持",
                    motor, self.config.drift_hold_current_ma, pos,
                )

    def setup_motors(self) -> None:
        # ID の小さい順にセットアップする。
        # デフォルトでは left_shoulder_yaw(ID1) から left_gripper(ID8)、
        # right_shoulder_yaw(ID11) から right_gripper(ID18) の順になる。
        motor_names_by_id = sorted(
            self.bus.motors,
            key=lambda motor_name: self.bus.motors[motor_name].id,
        )
        for motor in motor_names_by_id:
            input(
                f"Connect the controller board to the '{motor}' motor only and press ENTER.\n"
                f"  ({self._id_summary()})"
            )
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    # ------------------------------------------------------------------ #
    # メイン: Teleop -> action
    # ------------------------------------------------------------------ #
    def get_action(self) -> Dict[str, float]:
        """
        Present_Position の正規化値を lerobot の action dict に変換する。

        - gripper_motor_names に含まれるモータ: 0〜100 のまま
        - その他の関節: [-100, 100] -> [0, 100]
        """
        if not self.is_connected:
            raise DeviceNotConnectedError(self)

        with self._lock:
            present_position = self.bus.sync_read("Present_Position")

        values: Dict[str, float] = {}

        for name, norm_raw in present_position.items():
            norm_raw = float(norm_raw)
            base = self._base_motor_name(name)

            if self._is_gripper_motor(name):
                teleop = norm_raw  # すでに 0〜100
                logger.debug("OtterLeader %s: teleop=%7.3f", name, teleop)
            else:
                teleop = (norm_raw + 100.0) * 0.5  # [-100,100] -> [0,100]
                logger.debug(
                    "OtterLeader %s: base=%s norm_raw=%7.3f -> teleop=%7.3f",
                    name,
                    base,
                    norm_raw,
                    teleop,
                )

            # action key は "<motor_name>.pos"
            # 例: "left_elbow_pitch.pos", "right_wrist_roll.pos"
            values[f"{name}.pos"] = teleop

        return values

    def send_feedback(self, feedback: Dict[str, float]) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        try:
            self.bus.disconnect()
        except Exception as e:
            # USB 瞬断後はポート操作が I/O error になる。クリーンアップ失敗は
            # 警告に留め、二次トレースバックで本来のエラーを隠さない。
            logger.warning("OtterLeader: 切断時のシリアルエラーを無視します: %s", e)
        logger.info("%s disconnected.", self)
