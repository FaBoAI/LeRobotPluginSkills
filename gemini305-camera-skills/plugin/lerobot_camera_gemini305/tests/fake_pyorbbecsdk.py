# SPDX-License-Identifier: Apache-2.0
"""pyorbbecsdk のフェイク実装 (ハードウェア不要テスト用)。

conftest.py が sys.modules["pyorbbecsdk"] にこのモジュールを注入することで、
プラグインの全ロジック (デバイス選択・プロファイル選択・デコード・スレッド・
エラーパス) を実カメラなしで pytest できる。

STATE で挙動を制御する:
    devices:      接続デバイスのリスト [{"name", "serial", "pid"}]
    fail_open:    True なら get_device_by_index が OBError (udev 権限エラーを模擬)
    freeze:       True なら wait_for_frames が None を返し続ける (フレーム停止を模擬)
    drop_color:   True なら frameset に color を入れない (部分 frameset を模擬)
    drop_depth:   True なら frameset に depth を入れない (部分 frameset を模擬)
    wait_error:   True なら wait_for_frames が OBError (USB 切断などの実エラーを模擬)
    corrupt_mjpg: True なら MJPG フレームのデータを壊す (デコード失敗を模擬)
"""

import threading
import time

import cv2
import numpy as np

DEFAULT_DEVICE = {"name": "Orbbec Gemini 305", "serial": "FAKE0001", "pid": 0x0840}

STATE = {
    "devices": [dict(DEFAULT_DEVICE)],
    "fail_open": False,
    "freeze": False,
    "drop_color": False,
    "drop_depth": False,
    "wait_error": False,
    "corrupt_mjpg": False,
}


def reset_state():
    STATE["devices"] = [dict(DEFAULT_DEVICE)]
    STATE["fail_open"] = False
    STATE["freeze"] = False
    STATE["drop_color"] = False
    STATE["drop_depth"] = False
    STATE["wait_error"] = False
    STATE["corrupt_mjpg"] = False


class OBError(Exception):
    pass


class OBLogLevel:
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    FATAL = 4
    NONE = 5


class _Fmt:
    def __init__(self, label):
        self.label = label

    def __str__(self):
        return f"OBFormat.{self.label}"

    __repr__ = __str__


class OBFormat:
    RGB = _Fmt("RGB")
    MJPG = _Fmt("MJPG")
    YUYV = _Fmt("YUYV")
    Y16 = _Fmt("Y16")
    UNKNOWN_FORMAT = _Fmt("UNKNOWN")


class OBSensorType:
    COLOR_SENSOR = "color"
    DEPTH_SENSOR = "depth"


# 実機 (Gemini 305) のモード表の縮約版 (実測に合わせること)。
# カラー: 1280x800/1280x720 の無圧縮 (RGB/YUYV) は 30fps まで、60fps は MJPG のみ。
# 848x530 以下は無圧縮でも 60fps がある (実測 60.3fps @848x530 RGB)。デプス: Y16。
COLOR_MODES = {
    (1280, 800, 30): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
    (1280, 800, 60): [OBFormat.MJPG],
    (1280, 720, 30): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
    (1280, 720, 60): [OBFormat.MJPG],
    (848, 530, 30): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
    (848, 530, 60): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
    (640, 480, 30): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
    (640, 480, 60): [OBFormat.RGB, OBFormat.MJPG, OBFormat.YUYV],
}
DEPTH_MODES = {
    (1280, 800, 30): [OBFormat.Y16],
    (848, 530, 30): [OBFormat.Y16],
    (848, 480, 60): [OBFormat.Y16],
    (640, 480, 30): [OBFormat.Y16],
}
# 実機の get_default_video_stream_profile() は 848x530@30 を返す (1280x800 ではない)
DEFAULT_COLOR = (848, 530, 30)
DEPTH_SCALE = 0.1  # 実機と同じ 0.1mm/LSB
DEPTH_RAW_VALUE = 1000  # → 100mm 相当


class Context:
    @staticmethod
    def set_logger_level(level):
        pass

    def query_devices(self):
        return DeviceList()


class DeviceInfo:
    def get_firmware_version(self):
        return "fake-1.0.0"


class Device:
    def get_device_info(self):
        return DeviceInfo()


class DeviceList:
    def __init__(self):
        self._devices = list(STATE["devices"])

    def get_count(self):
        return len(self._devices)

    def get_device_name_by_index(self, i):
        return self._devices[i]["name"]

    def get_device_serial_number_by_index(self, i):
        return self._devices[i]["serial"]

    def get_device_pid_by_index(self, i):
        return self._devices[i]["pid"]

    def get_device_connection_type_by_index(self, i):
        return "USB3.2"

    def get_device_by_index(self, i):
        if STATE["fail_open"]:
            raise OBError("usbEnumerator openUsbDevice failed!")
        return Device()


class VideoStreamProfile:
    def __init__(self, w, h, fmt, fps, sensor):
        self._w, self._h, self._fmt, self._fps, self._sensor = w, h, fmt, fps, sensor

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h

    def get_fps(self):
        return self._fps

    def get_format(self):
        return self._fmt

    def as_video_stream_profile(self):
        return self


class StreamProfileList:
    def __init__(self, sensor_type):
        self._sensor = sensor_type
        modes = COLOR_MODES if sensor_type == OBSensorType.COLOR_SENSOR else DEPTH_MODES
        self._profiles = [
            VideoStreamProfile(w, h, fmt, fps, sensor_type)
            for (w, h, fps), fmts in modes.items()
            for fmt in fmts
        ]

    def get_count(self):
        return len(self._profiles)

    def get_stream_profile_by_index(self, i):
        return self._profiles[i]

    def get_default_video_stream_profile(self):
        w, h, fps = DEFAULT_COLOR
        return VideoStreamProfile(w, h, OBFormat.RGB, fps, self._sensor)

    def get_video_stream_profile(self, width=0, height=0, format=None, fps=0):
        for p in self._profiles:
            if width and p._w != width:
                continue
            if height and p._h != height:
                continue
            if fps and p._fps != fps:
                continue
            if format is not None and format is not OBFormat.UNKNOWN_FORMAT and p._fmt is not format:
                continue
            return p
        raise OBError(f"no matching profile {width}x{height}@{fps} {format}")


class _ColorFrame:
    """seq に応じて中身が変わる合成カラーフレーム。R=64, G=128, B=seq%256。"""

    def __init__(self, w, h, fmt, seq):
        self._w, self._h, self._fmt = w, h, fmt
        rgb = np.empty((h, w, 3), dtype=np.uint8)
        rgb[..., 0] = 64
        rgb[..., 1] = 128
        rgb[..., 2] = seq % 256
        if fmt is OBFormat.RGB:
            self._data = rgb.tobytes()
        elif fmt is OBFormat.MJPG:
            if STATE["corrupt_mjpg"]:
                self._data = b"\xff\xd8not-a-real-jpeg"
            else:
                ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                assert ok
                self._data = jpeg.tobytes()
        elif fmt is OBFormat.YUYV:
            # Y=一定, U=V=128 の単純な YUYV (デコード後はほぼグレー)
            yuyv = np.empty((h, w, 2), dtype=np.uint8)
            yuyv[..., 0] = 100 + seq % 50
            yuyv[..., 1] = 128
            self._data = yuyv.tobytes()
        else:
            raise OBError(f"unsupported fake color format {fmt}")

    def get_data(self):
        return self._data

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h

    def get_format(self):
        return self._fmt


class _DepthFrame:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._data = np.full((h, w), DEPTH_RAW_VALUE, dtype=np.uint16).tobytes()

    def get_data(self):
        return self._data

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h

    def get_depth_scale(self):
        return DEPTH_SCALE


class _FrameSet:
    def __init__(self, color, depth):
        self._color, self._depth = color, depth

    def get_color_frame(self):
        return self._color

    def get_depth_frame(self):
        return self._depth


class Config:
    def __init__(self):
        self.streams = []

    def enable_stream(self, profile):
        self.streams.append(profile)


class Pipeline:
    def __init__(self, device):
        self._device = device
        self._config = None
        self._started = False
        self._seq = 0
        self._lock = threading.Lock()

    def get_stream_profile_list(self, sensor_type):
        return StreamProfileList(sensor_type)

    def start(self, config):
        if not config.streams:
            raise OBError("no stream enabled")
        self._config = config
        self._started = True

    def stop(self):
        self._started = False

    def wait_for_frames(self, timeout_ms):
        if not self._started:
            raise OBError("pipeline not started")
        if STATE["wait_error"]:
            raise OBError("simulated device lost")
        if STATE["freeze"]:
            # 実 SDK は timeout_ms まで待って None を返すが、テスト高速化のため
            # 100ms 上限で切り上げる (早めの None 返しはプラグインには無害)
            time.sleep(min(timeout_ms, 100) / 1000.0)
            return None
        time.sleep(0.005)  # 実機のフレーム間隔を粗く模擬 (~200fps 上限)
        with self._lock:
            self._seq += 1
            seq = self._seq
        color = depth = None
        for p in self._config.streams:
            if p._sensor == OBSensorType.COLOR_SENSOR and not STATE["drop_color"]:
                color = _ColorFrame(p._w, p._h, p._fmt, seq)
            elif p._sensor == OBSensorType.DEPTH_SENSOR and not STATE["drop_depth"]:
                depth = _DepthFrame(p._w, p._h)
        return _FrameSet(color, depth)
