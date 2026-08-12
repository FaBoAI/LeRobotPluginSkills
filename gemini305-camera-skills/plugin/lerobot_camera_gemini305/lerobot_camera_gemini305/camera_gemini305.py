# SPDX-License-Identifier: Apache-2.0
"""Gemini305Camera — Orbbec Gemini 305 の LeRobot カメラ実装。

pyorbbecsdk (Orbbec SDK v2) を LeRobot と同一プロセスで直接使う。
構造は LeRobot 組み込みの RealSenseCamera に合わせている
(バックグラウンド読み取りスレッド + frame_lock + new_frame_event)。
depth 出力の規約も RealSense と同じ (H, W, 1) uint16 / 単位 mm。
"""

import logging
import time
from threading import Event, Lock, Thread
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from lerobot.cameras.camera import Camera
from lerobot.cameras.configs import ColorMode
from lerobot.cameras.utils import get_cv2_rotation
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.errors import DeviceNotConnectedError

from .configuration_gemini305 import ColorFormat, Gemini305CameraConfig

try:
    import pyorbbecsdk as ob
except ImportError:  # register_third_party_plugins の import を壊さない
    ob = None

logger = logging.getLogger(__name__)

INSTALL_HINT = (
    "pyorbbecsdk が見つかりません。`pip install pyorbbecsdk2 --no-deps` で"
    "インストールしてください (PyPI の `pyorbbecsdk` は v1 系 x86_64 のみなので不可)。"
)
# connect(warmup=True) が初回フレームを待つ上限 (再接続直後は 1 秒超えることがある)
FIRST_FRAME_TIMEOUT_S = 10.0

UDEV_HINT = (
    "USB デバイスを開けません。udev ルール未設置の可能性があります: "
    "sudo cp $(python -c 'import pyorbbecsdk, os; "
    "print(os.path.join(os.path.dirname(pyorbbecsdk.__file__), \"shared\", "
    "\"99-obsensor-libusb.rules\"))') /etc/udev/rules.d/ && "
    "sudo udevadm control --reload-rules && sudo udevadm trigger"
)

_context = None


def _get_context():
    """SDK の Context をプロセスで 1 つだけ作る (find_cameras と connect で共有)。"""
    global _context
    if _context is None:
        ob.Context.set_logger_level(ob.OBLogLevel.ERROR)  # SDK のコンソールログを抑制
        _context = ob.Context()
    return _context


def _ob_error() -> type[Exception]:
    return getattr(ob, "OBError", Exception)


class Gemini305Camera(Camera):
    """Orbbec Gemini 305 カメラ。

    例:
    ```python
    from lerobot_camera_gemini305 import Gemini305Camera, Gemini305CameraConfig

    cam = Gemini305Camera(Gemini305CameraConfig(fps=30, width=1280, height=800))
    cam.connect()
    frame = cam.async_read()          # (800, 1280, 3) uint8 RGB
    cam.disconnect()

    # 1 台構成ならシリアル省略可。解像度省略時はデバイスデフォルト (848x530@30)
    depth_cam = Gemini305Camera(Gemini305CameraConfig(use_depth=True))
    depth_cam.connect()
    depth = depth_cam.async_read_depth()  # (530, 848, 1) uint16 [mm]
    ```
    """

    config_class = Gemini305CameraConfig
    name = "gemini305"

    def __init__(self, config: Gemini305CameraConfig):
        if ob is None:
            raise ImportError(INSTALL_HINT)
        super().__init__(config)  # self.fps/width/height を設定
        self.config = config

        self.color_mode = config.color_mode
        self.use_rgb = config.use_rgb
        self.use_depth = config.use_depth
        self.warmup_s = config.warmup_s

        self._pipeline: Any | None = None
        self._device: Any | None = None
        self._started = False
        self._color_format_actual: ColorFormat | None = None

        self.thread: Thread | None = None
        self.stop_event: Event | None = None
        self.frame_lock: Lock = Lock()
        self.latest_color_frame: NDArray[Any] | None = None
        self.latest_depth_frame: NDArray[Any] | None = None
        # 鮮度判定・新着通知はストリーム別に持つ。共有にすると片方のストリームだけ
        # 停止したとき (部分 frameset は実機で起こる)、もう片方の到着で
        # 凍結フレームが「新鮮」と誤判定される。
        self.latest_color_ts: float | None = None
        self.latest_depth_ts: float | None = None
        self.new_color_event: Event = Event()
        self.new_depth_event: Event = Event()

        self.rotation: int | None = get_cv2_rotation(config.rotation)

        # capture_* は回転前のセンサー出力サイズ (rotation 90/270 で width/height が入れ替わる)
        self.capture_width: int | None = None
        self.capture_height: int | None = None
        if self.width and self.height:
            self.capture_width, self.capture_height = self.width, self.height
            if self.rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]:
                self.capture_width, self.capture_height = self.height, self.width

    def __str__(self) -> str:
        ident = self.config.serial_number_or_name or "auto"
        return f"{self.__class__.__name__}({ident})"

    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return self._pipeline is not None and self._started

    @staticmethod
    def find_cameras() -> list[dict[str, Any]]:
        """接続されている Orbbec デバイスを列挙する。

        シリアル・名前・PID はデバイスを開かずに取得する (使用中のカメラを
        邪魔しない)。ファームウェア等の詳細は開けた場合のみ付与する。
        """
        if ob is None:
            raise ImportError(INSTALL_HINT)
        found: list[dict[str, Any]] = []
        devs = _get_context().query_devices()
        for i in range(devs.get_count()):
            info: dict[str, Any] = {
                "type": "Gemini305",
                "id": devs.get_device_serial_number_by_index(i),
                "name": devs.get_device_name_by_index(i),
                "product_id": f"0x{devs.get_device_pid_by_index(i):04x}",
                "connection_type": devs.get_device_connection_type_by_index(i),
            }
            try:
                dev = devs.get_device_by_index(i)
                di = dev.get_device_info()
                info["firmware_version"] = di.get_firmware_version()
            except Exception:  # 使用中デバイスは開けない — 基本情報のみ返す
                pass
            found.append(info)
        return found

    # ------------------------------------------------------------------ #
    def _select_device(self) -> Any:
        """serial_number_or_name (または自動) でデバイスを選ぶ。"""
        devs = _get_context().query_devices()
        count = devs.get_count()
        entries = [
            (i, devs.get_device_name_by_index(i), devs.get_device_serial_number_by_index(i))
            for i in range(count)
        ]

        target = self.config.serial_number_or_name
        if target is None:
            if count == 0:
                raise ConnectionError(
                    f"No Orbbec device found for {self}. "
                    f"USB 接続と udev ルールを確認してください。{UDEV_HINT}"
                )
            if count > 1:
                raise ValueError(
                    f"Multiple Orbbec devices found: {[(n, s) for _, n, s in entries]}. "
                    "serial_number_or_name でどれかを指定してください。"
                )
            index = 0
        else:
            matches = [i for i, _, serial in entries if serial == target]
            if not matches:
                matches = [i for i, name, _ in entries if name == target]
            if not matches:
                raise ValueError(
                    f"No Orbbec device matching '{target}'. "
                    f"Available: {[(n, s) for _, n, s in entries]}"
                )
            if len(matches) > 1:
                serials = [entries[i][2] for i in matches]
                raise ValueError(
                    f"Multiple Orbbec devices named '{target}'. "
                    f"シリアル番号で指定してください: {serials}"
                )
            index = matches[0]

        try:
            return devs.get_device_by_index(index)
        except _ob_error() as e:
            raise ConnectionError(f"Failed to open {self}: {e}. {UDEV_HINT}") from e

    def _select_color_profile(self, plist: Any) -> Any:
        """要求 (width/height/fps) と color_format からカラープロファイルを選ぶ。"""
        if self.width is None:  # 全部 None → デバイスのデフォルトプロファイルの寸法を使う
            default = plist.get_default_video_stream_profile()
            w, h, fps = default.get_width(), default.get_height(), default.get_fps()
        else:
            w, h, fps = self.capture_width, self.capture_height, self.fps

        fmt_prefs = {
            ColorFormat.AUTO: [ColorFormat.RGB, ColorFormat.MJPG, ColorFormat.YUYV],
            ColorFormat.RGB: [ColorFormat.RGB],
            ColorFormat.MJPG: [ColorFormat.MJPG],
            ColorFormat.YUYV: [ColorFormat.YUYV],
        }[self.config.color_format]
        ob_formats = {
            ColorFormat.RGB: ob.OBFormat.RGB,
            ColorFormat.MJPG: ob.OBFormat.MJPG,
            ColorFormat.YUYV: ob.OBFormat.YUYV,
        }

        for fmt in fmt_prefs:
            try:
                profile = plist.get_video_stream_profile(w, h, ob_formats[fmt], fps)
            except _ob_error():
                continue
            if profile is not None:
                self._color_format_actual = fmt
                return profile

        available = self._summarize_profiles(plist)
        raise ConnectionError(
            f"{self}: no color profile for {w}x{h}@{fps} "
            f"(formats tried: {[f.value for f in fmt_prefs]}). "
            f"Available: {available}. "
            "注意: Gemini 305 の 1280x800 / 1280x720 の 60fps は MJPG のみ "
            "(848x530 以下は無圧縮 60fps も可)。"
        )

    @staticmethod
    def _summarize_profiles(plist: Any) -> list[str]:
        """プロファイル一覧を「WxH@fps fmt」文字列の重複なしリストに要約する。"""
        seen: dict[tuple, None] = {}
        for i in range(plist.get_count()):
            try:
                vp = plist.get_stream_profile_by_index(i).as_video_stream_profile()
            except Exception:
                continue
            key = (vp.get_width(), vp.get_height(), vp.get_fps(), str(vp.get_format()))
            seen.setdefault(key, None)
        return [f"{w}x{h}@{fps} {fmt}" for (w, h, fps, fmt) in seen]

    @check_if_already_connected
    def connect(self, warmup: bool = True) -> None:
        """カメラに接続し、ストリームを開始する。

        Raises:
            DeviceAlreadyConnectedError: すでに接続済み。
            ConnectionError: デバイスが見つからない / 開けない (udev) /
                要求プロファイルが存在しない。
            ValueError: serial_number_or_name の指定が曖昧・不一致。
        """
        self._device = self._select_device()
        pipeline = None
        pipeline_started = False
        try:
            pipeline = ob.Pipeline(self._device)
            config = ob.Config()

            if self.use_rgb:
                plist = pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR)
                color_profile = self._select_color_profile(plist)
                config.enable_stream(color_profile)
                # 解像度未指定の場合もここで capture_* が確定する。depth はカラーと
                # 同じ解像度で有効化する (食い違うと _postprocess_image の寸法検証で落ちる)。
                self._configure_capture_settings(color_profile)
            if self.use_depth:
                dlist = pipeline.get_stream_profile_list(ob.OBSensorType.DEPTH_SENSOR)
                w = self.capture_width or 0
                h = self.capture_height or 0
                fps = self.fps or 0
                try:
                    depth_profile = dlist.get_video_stream_profile(w, h, ob.OBFormat.Y16, fps)
                except _ob_error() as e:
                    raise ConnectionError(
                        f"{self}: no depth profile for {w or '?'}x{h or '?'}@{fps or '?'}. "
                        f"Available: {self._summarize_profiles(dlist)}. "
                        "depth はカラーと同じ解像度・fps で有効化されます "
                        "(1280x800 の depth は 30fps まで、60fps は 848x480 以下)。"
                    ) from e
                config.enable_stream(depth_profile)
                if not self.use_rgb:
                    self._configure_capture_settings(depth_profile)

            try:
                pipeline.start(config)
            except _ob_error() as e:
                raise ConnectionError(
                    f"Failed to start {self}: {e}. "
                    "他のプロセスが使用中でないか (find_cameras で確認)、"
                    f"USB3 接続かを確認してください。{UDEV_HINT}"
                ) from e
            pipeline_started = True

            self._pipeline = pipeline
            self._started = True
            self._start_read_thread()

            # 起動直後はフレームが来るまで少しかかるので最低 1 秒はウォームアップする。
            # 再接続直後などは初回フレームが 1 秒を超えることがある (実機で確認) ため、
            # 初回フレーム待ちには warmup_s とは別に猶予を設ける。
            self.warmup_s = max(self.warmup_s, 1)
            if warmup:
                warmup_read = self.async_read if self.use_rgb else self.async_read_depth
                start_time = time.time()
                while True:
                    elapsed = time.time() - start_time
                    if elapsed >= self.warmup_s and self._required_frames_present():
                        break
                    if elapsed >= max(self.warmup_s, FIRST_FRAME_TIMEOUT_S):
                        raise ConnectionError(
                            f"{self} failed to capture frames during warmup ({elapsed:.1f}s)."
                        )
                    try:
                        warmup_read(timeout_ms=1000)
                    except TimeoutError:
                        pass
                    time.sleep(0.05)
        except Exception:
            # どの失敗パスでも接続前の状態に完全に戻す。特に self._device を
            # 保持したままにするとデバイスが開きっぱなしになり (Orbbec は排他)、
            # 本オブジェクト破棄まで再接続も他プロセスからの利用もできなくなる。
            self._stop_read_thread()
            if pipeline_started:
                try:
                    pipeline.stop()
                except Exception:  # pragma: no cover
                    logger.warning(f"{self} pipeline stop failed during connect cleanup.")
            self._pipeline = None
            self._started = False
            self._device = None
            self._color_format_actual = None
            raise

        logger.info(f"{self} connected.")

    def _required_frames_present(self) -> bool:
        """有効化した全ストリームのフレームが 1 枚以上取得済みか。"""
        with self.frame_lock:
            return not (
                (self.use_rgb and self.latest_color_frame is None)
                or (self.use_depth and self.latest_depth_frame is None)
            )

    def _configure_capture_settings(self, profile: Any) -> None:
        """fps/width/height 未指定時に実際のプロファイル値を反映する。"""
        if self.fps is None:
            self.fps = profile.get_fps()
        if self.width is None or self.height is None:
            actual_width = int(profile.get_width())
            actual_height = int(profile.get_height())
            self.capture_width, self.capture_height = actual_width, actual_height
            if self.rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]:
                self.width, self.height = actual_height, actual_width
            else:
                self.width, self.height = actual_width, actual_height

    # ------------------------------------------------------------------ #
    def _decode_color(self, cf: Any) -> NDArray[Any]:
        """ColorFrame → RGB uint8 (H, W, 3)。フォーマット別のデコードを吸収する。"""
        fmt = self._color_format_actual
        data = np.frombuffer(cf.get_data(), dtype=np.uint8)
        h, w = cf.get_height(), cf.get_width()
        if fmt == ColorFormat.MJPG:
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if bgr is None:
                raise RuntimeError(f"{self}: MJPG decode failed.")
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if fmt == ColorFormat.YUYV:
            yuyv = data.reshape(h, w, 2)
            return cv2.cvtColor(yuyv, cv2.COLOR_YUV2RGB_YUYV)
        return data.reshape(h, w, 3)  # RGB

    def _decode_depth(self, df: Any) -> NDArray[np.uint16]:
        """DepthFrame → uint16 (H, W)、単位 mm。

        Gemini 305 の depth scale は 0.1mm/LSB。RealSense と同じ
        「uint16 = mm」規約に正規化する (0.1mm の精度は丸めで失われる)。
        """
        raw = np.frombuffer(df.get_data(), dtype=np.uint16).reshape(
            df.get_height(), df.get_width()
        )
        scale = df.get_depth_scale()
        if scale != 1.0:
            raw = np.rint(raw.astype(np.float32) * scale).astype(np.uint16)
        return raw

    def _postprocess_image(self, image: NDArray[Any], depth_frame: bool = False) -> NDArray[Any]:
        """寸法検証・BGR 変換・回転 (RealSenseCamera と同じ規約)。"""
        if depth_frame:
            h, w = image.shape
        else:
            h, w, c = image.shape
            if c != 3:
                raise RuntimeError(f"{self} frame channels={c} do not match expected 3 channels.")

        if h != self.capture_height or w != self.capture_width:
            raise RuntimeError(
                f"{self} frame width={w} or height={h} do not match configured "
                f"width={self.capture_width} or height={self.capture_height}."
            )

        processed = image
        if not depth_frame and self.color_mode == ColorMode.BGR:
            processed = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        if self.rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_180]:
            processed = cv2.rotate(processed, self.rotation)

        return processed

    def _read_loop(self) -> None:
        """バックグラウンドスレッド: wait_for_frames → デコード → latest_* 更新。"""
        stop_event = self.stop_event
        if stop_event is None:
            raise RuntimeError(f"{self}: stop_event is not initialized before starting read loop.")

        failure_count = 0
        while not stop_event.is_set():
            try:
                # wait_for_frames は純粋なタイムアウトでは None を返す (実測)。
                # OBError は USB 切断などの実エラーなので握りつぶさず
                # failure_count エスカレーションに乗せる。
                frames = self._pipeline.wait_for_frames(1000)
                if frames is None:
                    continue

                processed_color = processed_depth = None
                if self.use_rgb:
                    cf = frames.get_color_frame()
                    if cf is not None:
                        processed_color = self._postprocess_image(self._decode_color(cf))
                if self.use_depth:
                    df = frames.get_depth_frame()
                    if df is not None:
                        depth = self._postprocess_image(self._decode_depth(df), depth_frame=True)
                        processed_depth = depth[..., np.newaxis] if depth.ndim == 2 else depth

                if processed_color is None and processed_depth is None:
                    continue

                capture_time = time.perf_counter()
                with self.frame_lock:
                    if processed_color is not None:
                        self.latest_color_frame = processed_color
                        self.latest_color_ts = capture_time
                    if processed_depth is not None:
                        self.latest_depth_frame = processed_depth
                        self.latest_depth_ts = capture_time
                # frameset は片方のストリームだけのことがある (実機で発生) ため、
                # 新着通知と鮮度はストリーム別に管理する
                if processed_color is not None:
                    self.new_color_event.set()
                if processed_depth is not None:
                    self.new_depth_event.set()
                failure_count = 0

            except DeviceNotConnectedError:
                break
            except Exception as e:
                if failure_count <= 10:
                    failure_count += 1
                    logger.warning(f"Error reading frame in background thread for {self}: {e}")
                else:
                    raise RuntimeError(f"{self} exceeded maximum consecutive read failures.") from e

    def _start_read_thread(self) -> None:
        self._stop_read_thread()
        self.stop_event = Event()
        self.thread = Thread(target=self._read_loop, args=(), name=f"{self}_read_loop")
        self.thread.daemon = True
        self.thread.start()

    def _stop_read_thread(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=3.0)
            if self.thread.is_alive():  # pragma: no cover
                logger.warning(f"{self} read thread did not terminate within timeout.")
        self.thread = None
        self.stop_event = None
        with self.frame_lock:
            self.latest_color_frame = None
            self.latest_depth_frame = None
            self.latest_color_ts = None
            self.latest_depth_ts = None
            self.new_color_event.clear()
            self.new_depth_event.clear()

    # ------------------------------------------------------------------ #
    def _read(self, read_depth: bool = False) -> NDArray[Any]:
        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")
        event = self.new_depth_event if read_depth else self.new_color_event
        event.clear()
        return self._async_read(timeout_ms=10000, read_depth=read_depth)

    @check_if_not_connected
    def read(self, color_mode: ColorMode | None = None) -> NDArray[Any]:
        """カラーフレームを 1 枚同期取得する (新フレームを待つ)。

        Returns:
            np.ndarray: (H, W, 3) uint8。color_mode と rotation を適用済み。
        """
        if color_mode is not None:
            logger.warning(
                f"{self} read() color_mode parameter is deprecated and will be removed in future versions."
            )
        if not self.use_rgb:
            raise RuntimeError(f"{self}: cannot read color — camera was configured with use_rgb=False.")
        return self._read()

    @check_if_not_connected
    def read_depth(self) -> NDArray[Any]:
        """デプスフレームを 1 枚同期取得する。(H, W, 1) uint16 [mm]。"""
        if not self.use_depth:
            raise RuntimeError(
                f"Failed to capture depth frame '.read_depth()'. Depth stream is not enabled for {self}."
            )
        return self._read(read_depth=True)

    def _async_read(self, timeout_ms: float, read_depth: bool = False) -> NDArray[Any]:
        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")

        event = self.new_depth_event if read_depth else self.new_color_event
        if not event.wait(timeout=timeout_ms / 1000.0):
            raise TimeoutError(
                f"Timed out waiting for frame from camera {self} after {timeout_ms} ms. "
                f"Read thread alive: {self.thread.is_alive()}."
            )

        with self.frame_lock:
            frame = self.latest_depth_frame if read_depth else self.latest_color_frame
            event.clear()

        if frame is None:
            raise RuntimeError(f"Internal error: Event set but no frame available for {self}.")
        return frame

    @check_if_not_connected
    def async_read(self, timeout_ms: float = 200) -> NDArray[Any]:
        """バックグラウンドスレッドが取得した最新カラーフレームを返す (新着を待つ)。"""
        if not self.use_rgb:
            raise RuntimeError(f"{self}: cannot read color — camera was configured with use_rgb=False.")
        return self._async_read(timeout_ms=timeout_ms)

    @check_if_not_connected
    def async_read_depth(self, timeout_ms: float = 200) -> NDArray[np.uint16]:
        """最新デプスフレームを返す (新着を待つ)。(H, W, 1) uint16 [mm]。"""
        if not self.use_depth:
            raise RuntimeError(f"{self}: cannot read depth — camera was configured with use_depth=False.")
        return self._async_read(timeout_ms=timeout_ms, read_depth=True)

    def _read_latest(self, max_age_ms: int, read_depth: bool = False) -> NDArray[Any]:
        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")

        with self.frame_lock:
            frame = self.latest_depth_frame if read_depth else self.latest_color_frame
            timestamp = self.latest_depth_ts if read_depth else self.latest_color_ts

        if frame is None or timestamp is None:
            raise RuntimeError(f"{self} has not captured any frames yet.")

        age_ms = (time.perf_counter() - timestamp) * 1e3
        if age_ms > max_age_ms:
            raise TimeoutError(
                f"{self} latest frame is too old: {age_ms:.1f} ms (max allowed: {max_age_ms} ms)."
            )
        return frame

    @check_if_not_connected
    def read_latest(self, max_age_ms: int = 500) -> NDArray[Any]:
        """最新カラーフレームを待たずに返す (ピーク)。古すぎたら TimeoutError。"""
        if not self.use_rgb:
            raise RuntimeError(f"{self}: cannot read color — camera was configured with use_rgb=False.")
        return self._read_latest(max_age_ms=max_age_ms)

    @check_if_not_connected
    def read_latest_depth(self, max_age_ms: int = 500) -> NDArray[Any]:
        """最新デプスフレームを待たずに返す (ピーク)。(H, W, 1) uint16 [mm]。"""
        if not self.use_depth:
            raise RuntimeError(f"{self}: cannot read depth — camera was configured with use_depth=False.")
        return self._read_latest(max_age_ms=max_age_ms, read_depth=True)

    # ------------------------------------------------------------------ #
    def disconnect(self) -> None:
        """ストリームを停止して切断する。

        Raises:
            DeviceNotConnectedError: すでに切断済み。
        """
        if not self.is_connected and self.thread is None:
            raise DeviceNotConnectedError(
                f"Attempted to disconnect {self}, but it appears already disconnected."
            )

        if self.thread is not None:
            self._stop_read_thread()

        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except _ob_error() as e:  # pragma: no cover
                logger.warning(f"{self} pipeline stop failed: {e}")
            self._pipeline = None
        self._started = False
        self._device = None
        self._color_format_actual = None

        logger.info(f"{self} disconnected.")
