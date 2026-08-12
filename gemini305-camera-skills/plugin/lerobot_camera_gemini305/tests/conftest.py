# SPDX-License-Identifier: Apache-2.0
# フェイク pyorbbecsdk をプラグイン import より先に sys.modules へ注入する。
# 実 SDK がインストールされている環境でもテストは常にフェイクで動く
# (実カメラの検証は lerobot-gemini305-monitor で別途行う)。
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import fake_pyorbbecsdk  # noqa: E402

sys.modules["pyorbbecsdk"] = fake_pyorbbecsdk

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_fake_state():
    fake_pyorbbecsdk.reset_state()
    yield
    fake_pyorbbecsdk.reset_state()
