# SPDX-License-Identifier: Apache-2.0
"""HSBCamera プラグインのハードウェア不要テスト。

実カメラの代わりに fake_worker.py (同一の共有メモリプロトコル) を使う。
実行: <lerobot入りvenv>/bin/python -m pytest tests/ -v
"""

import os
import sys
import time

import numpy as np
import pytest

from lerobot.cameras.configs import CameraConfig, ColorMode
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from lerobot_camera_hsb import HSBCamera, HSBCameraConfig

FAKE_WORKER = os.path.join(os.path.dirname(__file__), "fake_worker.py")


def make_config(**kw):
    defaults = dict(
        camera_mode=1,  # 1920x1080@30
        hsb_python=sys.executable,  # フェイクワーカーは今の python で動く
        worker_script=FAKE_WORKER,
        connect_timeout_s=15.0,
        warmup_s=0.0,
    )
    defaults.update(kw)
    return HSBCameraConfig(**defaults)


@pytest.fixture
def camera():
    cam = HSBCamera(make_config())
    yield cam
    if cam.is_connected:
        cam.disconnect()


# ---- 登録と生成 ----------------------------------------------------------


def test_config_registered_as_hsb():
    assert CameraConfig.get_choice_class("hsb") is HSBCameraConfig


def test_config_derives_resolution_from_mode():
    cfg = make_config()
    assert (cfg.width, cfg.height, cfg.fps) == (1920, 1080, 30)
    cfg0 = make_config(camera_mode=0)
    assert (cfg0.width, cfg0.height, cfg0.fps) == (2560, 1984, 30)


def test_config_rejects_bad_mode():
    with pytest.raises(ValueError):
        make_config(camera_mode=9)


def test_config_rejects_mismatched_resolution():
    with pytest.raises(ValueError):
        make_config(camera_mode=1, width=640, height=480)


def test_make_cameras_from_configs_resolves_plugin_class():
    from lerobot.cameras.utils import make_cameras_from_configs

    cams = make_cameras_from_configs({"front": make_config()})
    assert isinstance(cams["front"], HSBCamera)


# ---- 接続とフレーム取得 ----------------------------------------------------


def test_connect_read_disconnect(camera):
    camera.connect(warmup=False)
    assert camera.is_connected
    frame = camera.read()
    assert frame.shape == (1080, 1920, 3)
    assert frame.dtype == np.uint8
    camera.disconnect()
    assert not camera.is_connected


def test_async_read_returns_new_frames(camera):
    camera.connect(warmup=False)
    f1 = camera.async_read(timeout_ms=2000)
    f2 = camera.async_read(timeout_ms=2000)
    # フェイクワーカーはフレームごとに B チャネルを変える
    assert f1[..., 2].mean() != f2[..., 2].mean()


def test_read_latest(camera):
    camera.connect(warmup=False)
    frame = camera.read_latest(max_age_ms=2000)
    assert frame.shape == (1080, 1920, 3)


def test_bgr_color_mode():
    cam = HSBCamera(make_config(color_mode=ColorMode.BGR))
    cam.connect(warmup=False)
    try:
        rgb_like = cam.read(color_mode=ColorMode.RGB)
        bgr = cam.read()
        assert np.array_equal(bgr[..., ::-1], rgb_like)
    finally:
        cam.disconnect()


def test_async_read_timeout():
    cam = HSBCamera(make_config())
    cam.connect(warmup=False)
    try:
        cam.async_read(timeout_ms=2000)
        # 新フレーム間隔 ~33ms より十分短いタイムアウトでも、
        # 同一フレームしか無い瞬間を突くのは不安定なので 1ms で確実に切らす
        with pytest.raises(TimeoutError):
            for _ in range(50):
                cam.async_read(timeout_ms=1)
    finally:
        cam.disconnect()


# ---- エラーパス ------------------------------------------------------------


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


def test_worker_failure_gives_helpful_error():
    cam = HSBCamera(make_config(connect_timeout_s=10.0))
    cam.config.worker_script = FAKE_WORKER
    # --fail を模擬するには worker がすぐ死ぬケース: fail フラグ付きラッパーを使う
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(
            "import sys, runpy\n"
            f"sys.argv = [sys.argv[0]] + sys.argv[1:] + ['--fail']\n"
            f"runpy.run_path({FAKE_WORKER!r}, run_name='__main__')\n"
        )
        wrapper = f.name
    cam.config.worker_script = wrapper
    try:
        with pytest.raises(ConnectionError) as ei:
            cam.connect()
        assert "simulated enumeration failure" in str(ei.value)
    finally:
        os.unlink(wrapper)


def test_shm_cleaned_after_disconnect(camera):
    camera.connect(warmup=False)
    shm_path = camera._shm_path
    assert os.path.exists(shm_path)
    camera.disconnect()
    assert not os.path.exists(shm_path)


def test_reconnect_after_disconnect(camera):
    camera.connect(warmup=False)
    camera.disconnect()
    camera.connect(warmup=False)
    frame = camera.read()
    assert frame.shape == (1080, 1920, 3)
