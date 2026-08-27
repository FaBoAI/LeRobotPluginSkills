"""リーダーのグリッパ読み値の向き診断。

使い方: リーダーを USB 接続した状態で
    /home/jetson/camera/lerobot060-venv/bin/python /home/jetson/RS/check_gripper_direction.py
15秒間、左右グリッパの読み値を表示する。
  前半: 両手を「開いた」状態で保持 → 後半: 両手を「閉じた」状態で保持。
基準 (データセット収録時の規約): 開 = left_gripper ≈ 86 / right_gripper ≈ 9。
"""

import glob
import time

from lerobot_teleoperator_otter_leader import OtterLeader, OtterLeaderConfig

port = sorted(glob.glob("/dev/ttyUSB*"))[0]
leader = OtterLeader(OtterLeaderConfig(port=port, id="blue"))
leader.connect(calibrate=False)
print(f"接続 OK ({port})。基準: 開=left≈86/right≈9、閉=left≈9/right≈86 付近")
print("前半7秒: 両手を開いて保持 → 後半7秒: 両手を閉じて保持\n")
try:
    t0 = time.monotonic()
    while (el := time.monotonic() - t0) < 15:
        a = leader.get_action()
        phase = "開のはず" if el < 7 else "閉のはず"
        print(f"t={el:4.1f}s [{phase}]  left_gripper={a['left_gripper.pos']:6.1f}  "
              f"right_gripper={a['right_gripper.pos']:6.1f}")
        time.sleep(1.0)
finally:
    leader.disconnect()
print("\n判定: 「開のはず」の行が 基準 (left≈86/right≈9) から大きく外れていれば、その側が反転")
