"""SolidWorks MathTransform 旋转块存储约定回归测试（纯 Python，不需要 CAD）。

背景（2026-10-26 实测）：SolidWorks `Transform2.ArrayData[0:9]` 是**列主序**——
连续存放局部 X/Y/Z 轴在装配体中的像，与常见的行主序 p'=R·p 互为转置。

证据：同一 18 件装配体按行主序写入 → 整机包围盒偏 493.3 mm（零件散架）；
按列主序写入 → 偏 2.1 mm。对称旋转阵（diag(-1,-1,1)）两种写法等价，
所以历史用例没有暴露差异。

运行：python tests/solidworks_transform_convention_regression.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from sw_assembly import (  # noqa: E402
    build_transform_data_axes,
    build_transform_data_axis,
    build_transform_data_matrix,
)

FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  <- {detail}"))
    if not ok:
        FAILS.append(name)


def close(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


# 1. 绕 Z +90°（右手）：局部 X → 世界 Y，局部 Y → 世界 -X
z90 = build_transform_data_axis("Z", math.pi / 2)
check("Z+90 前 9 位为列主序 [x_axis|y_axis|z_axis]",
      close(list(z90[:9]), [0, 1, 0, -1, 0, 0, 0, 0, 1]), z90[:9])
check("Z+90 平移槽在 [9:12]", close(list(z90[9:12]), [0, 0, 0]), z90[9:12])
check("Z+90 尾部固定为 [1,0,0,0]", close(list(z90[12:16]), [1, 0, 0, 0]), z90[12:16])

# 2. 绕 X +45°：局部 Y → (0, cos45, sin45)
x45 = list(build_transform_data_axis("X", math.pi / 4)[:9])
check("X+45 的 y_axis = (0, c, s)",
      close(x45[3:6], [0, math.cos(math.pi / 4), math.sin(math.pi / 4)], 1e-9), x45)

# 3. 对称阵：修复前后结果相同（历史用例不受影响）
diag = list(build_transform_data_matrix([-1, 0, 0, 0, -1, 0, 0, 0, 1])[:9])
check("diag(-1,-1,1) 保持不变", close(diag, [-1, 0, 0, 0, -1, 0, 0, 0, 1]), diag)

# 4. 行主序入参 → 列主序出参（转置关系）
rz = [0, -1, 0, 1, 0, 0, 0, 0, 1]  # 行主序 Rz(+90)
m = list(build_transform_data_matrix(rz)[:9])
check("build_transform_data_matrix 完成转置", close(m, [0, 1, 0, -1, 0, 0, 0, 0, 1]), m)

# 5. 轴向量入口与矩阵入口一致
ax = list(build_transform_data_axes((0, 1, 0), (-1, 0, 0), (0, 0, 1), (0.1, 0.2, 0.3)))
check("build_transform_data_axes 与 matrix 入口一致", close(ax[:9], m) and close(ax[9:12], [0.1, 0.2, 0.3]), ax)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("全部通过")
