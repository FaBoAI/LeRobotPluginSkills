# SPDX-License-Identifier: Apache-2.0
# register_third_party_plugins() がこのパッケージを import すると
# @CameraConfig.register_subclass("gemini305") が実行され、--robot.cameras の
# {"type": "gemini305", ...} が使えるようになる。
# Gemini305Camera の re-export は make_device_from_device_class() の解決候補 (a)
# 「Config クラスの親パッケージ」を満たすために必須。
from .configuration_gemini305 import ColorFormat, Gemini305CameraConfig
from .camera_gemini305 import Gemini305Camera

__all__ = ["Gemini305CameraConfig", "Gemini305Camera", "ColorFormat"]
