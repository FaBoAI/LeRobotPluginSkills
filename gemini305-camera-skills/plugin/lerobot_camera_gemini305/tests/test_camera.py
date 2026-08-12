# SPDX-License-Identifier: Apache-2.0
"""Gemini305Camera プラグインのハードウェア不要テスト。

実カメラの代わりに fake_pyorbbecsdk (conftest.py が sys.modules に注入) を使う。
実行: <lerobot入りvenv>/bin/python -m pytest tests/ -v
"""

import numpy as np
import pytest

from lerobot.cameras.configs import CameraConfig, ColorMode, Cv2Rotation
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

import fake_pyorbbecsdk as fake
from lerobot_camera_gemini305 import ColorFormat, Gemini305Camera, Gemini305CameraConfig


def make_config(**kw):
    defaults = dict(warmup_s=1)
    defaults.update(kw)
    return Gemini305CameraConfig(**defaults)


@pytest.fixture
def camera():
    cam = Gemini305Camera(make_config())
    yield cam
    if cam.is_connected:
        cam.disconnect()


# ---- 登録と生成 ----------------------------------------------------------


def test_config_registered_as_gemini305():
    assert CameraConfig.get_choice_class("gemini305") is Gemini305CameraConfig


def test_config_rejects_partial_resolution():
    with pytest.raises(ValueError):
        make_config(width=1280, height=800)  # fps 欠け


def test_config_rejects_no_streams():
    with pytest.raises(ValueError):
        make_config(use_rgb=False, use_depth=False)


def test_config_rejects_bad_color_format():
    with pytest.raises(ValueError):
        make_config(color_format="jpeg2000")


def test_make_cameras_from_configs_resolves_plugin_class():
    from lerobot.cameras.utils import make_cameras_from_configs

    cams = make_cameras_from_configs({"wrist": make_config()})
    assert isinstance(cams["wrist"], Gemini305Camera)


def test_find_cameras_lists_fake_device():
    found = Gemini305Camera.find_cameras()
    assert len(found) == 1
    assert found[0]["id"] == "FAKE0001"
    assert found[0]["name"] == "Orbbec Gemini 305"
    assert found[0]["product_id"] == "0x0840"


# ---- 接続とフレーム取得 ----------------------------------------------------


def test_connect_read_disconnect(camera):
    camera.connect(warmup=False)
    assert camera.is_connected
    # デバイスのデフォルトプロファイル 848x530@30 が反映される (実機と同じ)
    assert (camera.width, camera.height, camera.fps) == (848, 530, 30)
    frame = camera.read()
    assert frame.shape == (530, 848, 3)
    assert frame.dtype == np.uint8
    camera.disconnect()
    assert not camera.is_connected


def test_connect_with_warmup(camera):
    camera.connect(warmup=True)
    assert camera.latest_color_frame is not None


def test_async_read_returns_new_frames(camera):
    camera.connect(warmup=False)
    f1 = camera.async_read(timeout_ms=2000)
    f2 = camera.async_read(timeout_ms=2000)
    # フェイクはフレームごとに B チャネルを変える
    assert f1[..., 2].mean() != f2[..., 2].mean()


def test_read_latest(camera):
    camera.connect(warmup=False)
    camera.async_read(timeout_ms=2000)
    frame = camera.read_latest(max_age_ms=2000)
    assert frame.shape == (530, 848, 3)


def test_read_latest_stale_raises(camera):
    camera.connect(warmup=False)
    camera.async_read(timeout_ms=2000)
    fake.STATE["freeze"] = True
    import time

    time.sleep(0.3)
    with pytest.raises(TimeoutError):
        camera.read_latest(max_age_ms=100)


def test_async_read_timeout_when_frozen(camera):
    import time

    camera.connect(warmup=False)
    camera.async_read(timeout_ms=2000)
    fake.STATE["freeze"] = True
    time.sleep(0.1)  # freeze 前に飛行中だった最後のフレームを着地させる
    try:
        camera.async_read(timeout_ms=50)  # 飛行中フレームがあれば消費
    except TimeoutError:
        return  # すでに新フレームが無ければこれで OK
    with pytest.raises(TimeoutError):
        camera.async_read(timeout_ms=200)


def test_bgr_color_mode():
    cam = Gemini305Camera(make_config(color_mode=ColorMode.BGR))
    cam.connect(warmup=False)
    try:
        bgr = cam.read()
        # フェイクの RGB は (R=64, G=128, B=seq) → BGR では [seq, 128, 64]
        assert bgr[0, 0, 1] == 128
        assert bgr[0, 0, 2] == 64
    finally:
        cam.disconnect()


def test_rotation_90_swaps_dimensions():
    cam = Gemini305Camera(
        make_config(fps=30, width=800, height=1280, rotation=Cv2Rotation.ROTATE_90)
    )
    cam.connect(warmup=False)
    try:
        assert (cam.capture_width, cam.capture_height) == (1280, 800)
        frame = cam.read()
        assert frame.shape == (1280, 800, 3)
    finally:
        cam.disconnect()


# ---- カラーフォーマット選択 -------------------------------------------------


def test_auto_prefers_rgb_at_30fps(camera):
    camera.connect(warmup=False)
    assert camera._color_format_actual == ColorFormat.RGB


def test_auto_falls_back_to_mjpg_at_60fps():
    cam = Gemini305Camera(make_config(fps=60, width=1280, height=800))
    cam.connect(warmup=False)
    try:
        assert cam._color_format_actual == ColorFormat.MJPG
        frame = cam.read()
        assert frame.shape == (800, 1280, 3)
    finally:
        cam.disconnect()


def test_explicit_yuyv():
    cam = Gemini305Camera(make_config(color_format="yuyv"))
    cam.connect(warmup=False)
    try:
        assert cam._color_format_actual == ColorFormat.YUYV
        frame = cam.read()
        assert frame.shape == (530, 848, 3)
    finally:
        cam.disconnect()


def test_unavailable_profile_raises_with_available_list():
    cam = Gemini305Camera(make_config(fps=999, width=64, height=64))
    with pytest.raises(ConnectionError) as ei:
        cam.connect()
    assert "1280x800" in str(ei.value)  # 利用可能なモードが列挙される


# ---- デプス ---------------------------------------------------------------


def test_depth_read():
    cam = Gemini305Camera(make_config(fps=30, width=1280, height=800, use_depth=True))
    cam.connect(warmup=False)
    try:
        depth = cam.read_depth()
        assert depth.shape == (800, 1280, 1)
        assert depth.dtype == np.uint16
        # フェイクの raw=1000, scale=0.1 → 100mm に正規化される
        assert int(depth[0, 0, 0]) == 100
    finally:
        cam.disconnect()


def test_async_read_depth():
    cam = Gemini305Camera(make_config(use_depth=True))
    cam.connect(warmup=False)
    try:
        depth = cam.async_read_depth(timeout_ms=2000)
        assert depth.shape == (530, 848, 1)
    finally:
        cam.disconnect()


def test_read_latest_depth_returns_depth_not_color():
    # 回帰テスト: read_latest_depth が read_depth=True を渡し忘れて
    # カラーフレームを返していたバグ (実機のモニタ CLI で発覚)
    cam = Gemini305Camera(make_config(use_depth=True))
    cam.connect(warmup=False)
    try:
        cam.async_read_depth(timeout_ms=2000)
        d = cam.read_latest_depth(max_age_ms=2000)
        assert d.dtype == np.uint16
        assert d.shape == (530, 848, 1)
    finally:
        cam.disconnect()


def test_depth_matches_default_color_resolution():
    # 解像度未指定 + use_depth: depth はカラーのデフォルト解像度 (848x530) に揃う。
    # カラー確定前に depth を「任意プロファイル」で選んで食い違うのは回帰バグ。
    cam = Gemini305Camera(make_config(use_depth=True))
    cam.connect(warmup=False)
    try:
        color = cam.async_read(timeout_ms=2000)
        depth = cam.async_read_depth(timeout_ms=2000)
        assert color.shape[:2] == depth.shape[:2] == (530, 848)
    finally:
        cam.disconnect()


def test_depth_only_camera():
    cam = Gemini305Camera(make_config(use_rgb=False, use_depth=True))
    cam.connect(warmup=False)
    try:
        depth = cam.async_read_depth(timeout_ms=2000)
        assert depth.shape == (800, 1280, 1)
        with pytest.raises(RuntimeError):
            cam.read()
    finally:
        cam.disconnect()


def test_depth_disabled_raises():
    cam = Gemini305Camera(make_config())
    cam.connect(warmup=False)
    try:
        with pytest.raises(RuntimeError):
            cam.read_depth()
    finally:
        cam.disconnect()


# ---- デバイス選択とエラーパス ------------------------------------------------


def test_no_device_raises_connection_error():
    fake.STATE["devices"] = []
    cam = Gemini305Camera(make_config())
    with pytest.raises(ConnectionError):
        cam.connect()


def test_multiple_devices_require_serial():
    fake.STATE["devices"] = [
        {"name": "Orbbec Gemini 305", "serial": "AAA", "pid": 0x0840},
        {"name": "Orbbec Gemini 305", "serial": "BBB", "pid": 0x0840},
    ]
    cam = Gemini305Camera(make_config())
    with pytest.raises(ValueError, match="Multiple"):
        cam.connect()


def test_select_by_serial():
    fake.STATE["devices"] = [
        {"name": "Orbbec Gemini 305", "serial": "AAA", "pid": 0x0840},
        {"name": "Orbbec Gemini 305", "serial": "BBB", "pid": 0x0840},
    ]
    cam = Gemini305Camera(make_config(serial_number_or_name="BBB"))
    cam.connect(warmup=False)
    try:
        assert cam.is_connected
    finally:
        cam.disconnect()


def test_wrong_serial_lists_available():
    cam = Gemini305Camera(make_config(serial_number_or_name="NOPE"))
    with pytest.raises(ValueError) as ei:
        cam.connect()
    assert "FAKE0001" in str(ei.value)


def test_open_failure_mentions_udev():
    fake.STATE["fail_open"] = True
    cam = Gemini305Camera(make_config())
    with pytest.raises(ConnectionError) as ei:
        cam.connect()
    assert "udev" in str(ei.value)


def test_double_connect_raises(camera):
    camera.connect(warmup=False)
    with pytest.raises(DeviceAlreadyConnectedError):
        camera.connect()


def test_read_before_connect_raises(camera):
    with pytest.raises(DeviceNotConnectedError):
        camera.read()


def test_disconnect_before_connect_raises(camera):
    with pytest.raises(DeviceNotConnectedError):
        camera.disconnect()


def test_reconnect_after_disconnect(camera):
    camera.connect(warmup=False)
    camera.disconnect()
    camera.connect(warmup=False)
    frame = camera.read()
    assert frame.shape == (530, 848, 3)


# ---- 敵対的レビュー起因の回帰テスト ------------------------------------------


def test_name_based_selection():
    # serial_number_or_name はシリアル不一致時にデバイス名でもマッチする (公開仕様)
    cam = Gemini305Camera(make_config(serial_number_or_name="Orbbec Gemini 305"))
    cam.connect(warmup=False)
    try:
        assert cam.is_connected
    finally:
        cam.disconnect()


def test_duplicate_name_requires_serial():
    fake.STATE["devices"] = [
        {"name": "Orbbec Gemini 305", "serial": "AAA", "pid": 0x0840},
        {"name": "Orbbec Gemini 305", "serial": "BBB", "pid": 0x0840},
    ]
    cam = Gemini305Camera(make_config(serial_number_or_name="Orbbec Gemini 305"))
    with pytest.raises(ValueError, match="シリアル"):
        cam.connect()


def _poll_until_thread_death(cam, deadline_s=5.0):
    """読み取りスレッドの failure_count エスカレーションによる死亡を待つ。"""
    import time

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            cam.async_read(timeout_ms=200)
        except TimeoutError:
            continue
        except RuntimeError as e:
            return e
    return None


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_wait_error_escalates_to_thread_death(camera):
    # 回帰テスト: wait_for_frames の OBError (USB 切断等) をタイムアウトと
    # 同一視して握りつぶすと、スレッドが永久スピンして明確な失敗にならない
    camera.connect(warmup=False)
    camera.async_read(timeout_ms=2000)
    fake.STATE["wait_error"] = True
    err = _poll_until_thread_death(camera)
    assert err is not None
    assert "read thread is not running" in str(err)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_mjpg_decode_failure_escalates():
    cam = Gemini305Camera(make_config(fps=60, width=1280, height=800))  # MJPG
    cam.connect(warmup=False)
    try:
        cam.async_read(timeout_ms=2000)
        fake.STATE["corrupt_mjpg"] = True
        err = _poll_until_thread_death(cam)
        assert err is not None
        assert "read thread is not running" in str(err)
    finally:
        if cam.is_connected:
            cam.disconnect()


def test_partial_frameset_depth_only_still_notifies_depth():
    # 実機の frameset は color/depth の片方だけのことがある。
    # 新着通知が color 依存だと depth 読者が color 停止に巻き込まれる (回帰)
    cam = Gemini305Camera(make_config(use_depth=True))
    cam.connect(warmup=False)
    try:
        cam.async_read(timeout_ms=2000)
        fake.STATE["drop_color"] = True
        d1 = cam.async_read_depth(timeout_ms=2000)
        d2 = cam.async_read_depth(timeout_ms=2000)
        assert d1.shape == d2.shape == (530, 848, 1)
    finally:
        cam.disconnect()


def test_stale_depth_not_masked_by_live_color():
    # 回帰テスト: 鮮度タイムスタンプを color/depth で共有すると、depth だけ
    # 停止しても color が流れる限り凍結 depth が「新鮮」と誤判定される
    import time

    cam = Gemini305Camera(make_config(use_depth=True))
    cam.connect(warmup=False)
    try:
        cam.async_read_depth(timeout_ms=2000)
        fake.STATE["drop_depth"] = True
        time.sleep(0.3)
        cam.read_latest(max_age_ms=1000)  # color は生きている
        with pytest.raises(TimeoutError):
            cam.read_latest_depth(max_age_ms=100)
    finally:
        cam.disconnect()


def test_warmup_timeout_cleans_up(monkeypatch):
    import lerobot_camera_gemini305.camera_gemini305 as cg

    monkeypatch.setattr(cg, "FIRST_FRAME_TIMEOUT_S", 1.5)
    fake.STATE["freeze"] = True
    cam = Gemini305Camera(make_config())
    with pytest.raises(ConnectionError, match="warmup"):
        cam.connect(warmup=True)
    # 失敗後は接続前の状態に完全に戻る (デバイスハンドルも解放)
    assert not cam.is_connected
    assert cam._device is None
    assert cam._pipeline is None
    with pytest.raises(DeviceNotConnectedError):
        cam.disconnect()
    # 復旧後は同じオブジェクトで再接続できる
    fake.STATE["freeze"] = False
    cam.connect(warmup=False)
    try:
        assert cam.read().shape == (530, 848, 3)
    finally:
        cam.disconnect()


def test_connect_failure_releases_device():
    # 回帰テスト: connect 失敗パスで self._device を保持すると、Orbbec は
    # 排他 open なので他プロセスも自分の再接続もデバイスを開けなくなる
    cam = Gemini305Camera(make_config(fps=999, width=64, height=64))
    with pytest.raises(ConnectionError):
        cam.connect()
    assert cam._device is None
    assert cam._pipeline is None
