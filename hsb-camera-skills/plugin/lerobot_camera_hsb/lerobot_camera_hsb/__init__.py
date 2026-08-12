# SPDX-License-Identifier: Apache-2.0
# register_third_party_plugins() がこのパッケージを import すると
# @CameraConfig.register_subclass("hsb") が実行され、--robot.cameras の
# {"type": "hsb", ...} が使えるようになる。
# HSBCamera の re-export は make_device_from_device_class() の解決候補 (a)
# 「Config クラスの親パッケージ」を満たすために必須。
from .configuration_hsb import HSBCameraConfig
from .camera_hsb import HSBCamera

__all__ = ["HSBCameraConfig", "HSBCamera"]
