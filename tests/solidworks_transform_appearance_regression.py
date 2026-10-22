"""
变换 / 材质 / 特征守卫 回归测试。

覆盖 2026-10 修复的三类缺陷，防止再次静默退化：

1. 组件变换（sw_assembly）
   - 旋转矩阵对 X/Y/Z 三轴的数值正确性
   - `build_transform_data_axis("X", …)` 与旧 `build_transform_data_x(…)` 逐位等价
   - `ArrayData` 必须用 `VARIANT(VT_ARRAY|VT_R8)` 写入（普通 tuple 会被 COM 丢弃旋转块）
   - 真实装配体：绕 Z 转 72° 后旋转块落地、平移保留、顶点角精确位移

2. 特征守卫（sw_part._require_feature）
   - 返回 None 时必须抛 RuntimeError，且错误信息带上下文
   - 非 None 时原样返回

3. 物理材质（IPartDoc.SetMaterialPropertyName2）
   - 材质名读回一致（含"聚乙烯"这类静默失败名必须被识别为失败）
   - 设材质后外观必须重新施加（顺序：材质 → 外观）

用法：
    # 只跑纯 Python 部分（无需 SolidWorks）
    python tests/solidworks_transform_appearance_regression.py

    # 追加真实 SolidWorks 部分
    python tests/solidworks_transform_appearance_regression.py --live

结果同时写入 JSON 并打印到 stdout；退出码 0 表示全部通过。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pythoncom  # noqa: E402
from win32com.client import VARIANT  # noqa: E402

from sw_assembly import (  # noqa: E402
    _rotation_matrix_axis,
    apply_component_transform_axis,
    build_transform_data_axis,
    build_transform_data_matrix,
    build_transform_data_x,
)
from sw_part import _require_feature  # noqa: E402
from sw_patterns import (  # noqa: E402
    hex_lattice_2d,
    min_pair_distance,
    tangent_pair_count,
)


CHECKS: list[dict] = []


def check(name: str, passed: bool, detail: object = "") -> None:
    """记录一项断言。"""
    CHECKS.append({"name": name, "passed": bool(passed), "detail": str(detail)})


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    """浮点近似相等。"""
    return abs(a - b) <= tol


# ---------------------------------------------------------------- 纯 Python 部分
def run_pure_checks() -> None:
    """不依赖 SolidWorks 的断言。"""
    # 1) 三轴旋转矩阵
    identity = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0]
    check("X 轴 0° 为单位阵", all(close(a, b) for a, b in zip(_rotation_matrix_axis("X", 0.0), identity)))
    check("Y 轴 0° 为单位阵", all(close(a, b) for a, b in zip(_rotation_matrix_axis("Y", 0.0), identity)))
    check("Z 轴 0° 为单位阵", all(close(a, b) for a, b in zip(_rotation_matrix_axis("Z", 0.0), identity)))

    z90 = _rotation_matrix_axis("Z", math.pi / 2)
    check("Z 轴 +90° 矩阵", all(close(a, b, 1e-12) for a, b in
                            zip(z90, [0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0])), z90)

    x30 = _rotation_matrix_axis("X", math.radians(30))
    check("X 轴 +30° 矩阵", close(x30[4], math.cos(math.radians(30))) and close(x30[5], -math.sin(math.radians(30))), x30)

    # 2) 非法轴
    try:
        _rotation_matrix_axis("W", 0.1)
        check("非法轴抛 ValueError", False, "未抛错")
    except ValueError:
        check("非法轴抛 ValueError", True)

    # 3) 向后兼容：X 轴等价于旧实现
    old = build_transform_data_x(0.1, 0.2, 0.3, math.radians(45))
    new = build_transform_data_axis("X", math.radians(45), (0.1, 0.2, 0.3))
    check("build_transform_data_axis('X') 与旧实现逐位相等", old == new, (old, new))

    # 4) 16 元布局：9 旋转 + 3 平移 + 1 缩放 + 3 保留
    arr = build_transform_data_matrix([1.0] * 9, (1.0, 2.0, 3.0))
    check("矩阵数组长度 16", len(arr) == 16, len(arr))
    check("平移落位 9..11", arr[9:12] == (1.0, 2.0, 3.0), arr[9:12])
    check("缩放位 12 = 1.0", arr[12] == 1.0, arr[12])

    # 5) 守卫：None 必须抛错且带上下文
    try:
        _require_feature(None, "测试特征", {"草图": "草图1", "depth_mm": 12.5})
        check("_require_feature(None) 抛错", False, "未抛错")
    except RuntimeError as exc:
        msg = str(exc)
        check("_require_feature(None) 抛错", True)
        check("错误信息含上下文键值", "草图=草图1" in msg and "depth_mm=12.5" in msg, msg)

    sentinel = object()
    check("_require_feature 非 None 原样返回", _require_feature(sentinel, "x") is sentinel)

    # 6) VARIANT 包装：长度必须为 16（这是 ArrayData 唯一可靠写入形式）
    packed = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in arr])
    check("VARIANT 包装成功", packed is not None)

    # 7) 六方点阵：相切由构造保证（点距 = spacing），区域由谓词表达
    spacing = 5.5
    pts = hex_lattice_2d(spacing, lambda x, y: 10 <= math.hypot(x, y) <= 30)
    check("六方点阵非空", len(pts) > 0, len(pts))
    mn = min_pair_distance(pts)
    check("最小中心距 = spacing（相切不重叠）", mn >= spacing - 1e-6, round(mn, 6))
    check("存在严格相切配对", tangent_pair_count(pts, spacing) > 0, tangent_pair_count(pts, spacing))
    check("谓词过滤生效（全部落在区域内）",
          all(10 - 1e-9 <= math.hypot(x, y) <= 30 + 1e-9 for x, y in pts))
    try:
        hex_lattice_2d(0.0, lambda x, y: True)
        check("spacing 非正抛 ValueError", False, "未抛错")
    except ValueError:
        check("spacing 非正抛 ValueError", True)

    # 8) rotational_copies：N 次旋转对称 → 质心精确归轴（点距关系不变）
    from sw_patterns import rotational_copies
    # 楔形必须与旋转间隔匹配（5×72° → 每份 72° 宽），且点要向内缩半个 spacing，
    # 否则相邻复制体在共享边界上的点会重合（与电缆扇区谓词的内缩同款）
    half_w = math.radians(36)
    local = hex_lattice_2d(spacing, lambda x, y: (
        10 <= math.hypot(x, y) <= 30
        and abs(math.atan2(y, x)) + (spacing / 2) / max(math.hypot(x, y), 1e-9) <= half_w))
    allpts = rotational_copies(local, 5, 72.0)
    cx = sum(x for x, y in allpts) / len(allpts)
    cy = sum(y for x, y in allpts) / len(allpts)
    check("旋转复制后质心精确归轴", math.hypot(cx, cy) <= 1e-9, (round(cx, 9), round(cy, 9)))
    mn2 = min_pair_distance(allpts)
    check("旋转复制不破坏相切", mn2 >= spacing - 1e-6, round(mn2, 6))


# ---------------------------------------------------------------- 真实 SolidWorks
def run_live_checks(output_dir: Path, visible: bool = True, wait_seconds: int = 12) -> None:
    """需要 SolidWorks 的断言。"""
    from sw_assembly import add_component
    from sw_session import SolidWorksSession

    session = SolidWorksSession(version=None, wait_seconds=wait_seconds, max_documents=16)
    # 1) 造一个种子零件
    seed = output_dir / "transform_seed.sldprt"
    model = session.new_part()
    model.SketchManager.InsertSketch(True)
    model.SketchManager.CreateCircle(0, 0, 0, 0.02, 0, 0)
    model.SketchManager.InsertSketch(True)
    model.ClearSelection2(True)
    model.Extension.SelectByID2("", "SKETCH", 0, 0, 0, False, 0, VARIANT(pythoncom.VT_DISPATCH, None), 0)
    model.FeatureManager.FeatureExtrusion3(
        True, False, False, 0, 0, 0.1, 0.0, False, False, False, False, 0.0, 0.0,
        False, False, False, False, True, False, True, 0, 0.0, False)
    session.save(model, str(seed))
    session.close_owned_documents()

    # 2) 组件绕 Z 转 72°
    asm = session.new_assembly()
    comp = add_component(asm, str(seed), 0, 0, 0, "", session.sw)
    check("装配体添加组件", comp is not None)
    if comp is None:
        return
    asm.ForceRebuild3(False)
    before = [float(v) for v in comp.Transform2.ArrayData]
    ok = apply_component_transform_axis(comp, axis="Z", angle_rad=math.radians(72), preserve_translation=True)
    after = [float(v) for v in comp.Transform2.ArrayData]
    c, s = math.cos(math.radians(72)), math.sin(math.radians(72))
    check("apply 返回 True", ok is True)
    check("旋转块落地", close(after[0], c, 1e-6) and close(after[1], -s, 1e-6) and close(after[3], s, 1e-6),
          [round(v, 6) for v in after[:9]])
    check("平移被保留", all(close(after[i], before[i], 1e-9) for i in (9, 10, 11)),
          (before[9:12], after[9:12]))

    # 3) 顶点角位移
    r, a0 = 20.0, math.radians(0.0)
    px, py = r * math.cos(a0), r * math.sin(a0)
    nx = after[0] * px + after[1] * py + after[2] * 0 + after[9] * 1000
    ny = after[3] * px + after[4] * py + after[5] * 0 + after[10] * 1000
    ang = math.degrees(math.atan2(ny, nx)) % 360
    check("顶点角 0° → 72°", abs(ang - 72.0) < 0.01, round(ang, 4))
    check("半径不变", abs(math.hypot(nx, ny) - r) < 1e-6, round(math.hypot(nx, ny), 6))

    # 4) 物理材质：读回一致
    part = session.new_part()
    part.SetMaterialPropertyName2("", "solidworks materials.sldmat", "铜")
    back = part.GetMaterialPropertyName2("", VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, ""))
    check("材质设为铜并读回", back == "铜", back)

    part.SetMaterialPropertyName2("", "solidworks materials.sldmat", "聚乙烯")
    back2 = part.GetMaterialPropertyName2("", VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, ""))
    check("库中无'聚乙烯'时读回不匹配（静默失败可识别）", back2 != "聚乙烯", back2)

    part.SetMaterialPropertyName2("", "solidworks materials.sldmat", "1060 合金")
    back3 = part.GetMaterialPropertyName2("", VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, ""))
    check("材质设为 1060 合金并读回", back3 == "1060 合金", back3)

    # 5) 外观必须在材质之后施加，否则被覆盖
    from sw_appearance import material_values, material_variant
    part.MaterialPropertyValues = material_variant(
        material_values("#B4703A", ambient=0.34, diffuse=0.58, specular=0.88, shininess=0.72))
    rb = [float(v) for v in part.MaterialPropertyValues]
    check("材质之后设外观仍生效", close(rb[0], 0.706, 1e-3) and close(rb[5], 0.88, 1e-3),
          [round(v, 3) for v in rb])
    session.close_owned_documents()


# ---------------------------------------------------------------- 入口
def parse_args() -> argparse.Namespace:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="变换/材质/守卫 回归测试")
    parser.add_argument("--live", action="store_true", help="追加需要 SolidWorks 的断言")
    parser.add_argument("--hidden", action="store_true", help="隐藏 SolidWorks 窗口")
    parser.add_argument("--output-dir", default=None, help="结果输出目录")
    parser.add_argument("--wait-seconds", type=int, default=15, help="连接 SolidWorks 的等待秒数")
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir \
        else Path(tempfile.gettempdir()) / "solidworks_skill_regression"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_pure_checks()
    live_error = None
    if args.live:
        try:
            run_live_checks(output_dir, visible=not args.hidden, wait_seconds=args.wait_seconds)
        except Exception as exc:  # 真实 SolidWorks 不可用时如实记录，不伪装成通过
            live_error = {"error": str(exc), "traceback": traceback.format_exc()}

    failed = [c for c in CHECKS if not c["passed"]]
    result = {
        "status": "ok" if (not failed and live_error is None) else "failed",
        "live_requested": args.live,
        "total": len(CHECKS),
        "passed": len(CHECKS) - len(failed),
        "failed": failed,
        "live_error": live_error,
        "checks": CHECKS,
        "output_dir": str(output_dir),
    }
    (output_dir / "transform_appearance_regression_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "total", "passed", "live_requested")},
                     ensure_ascii=False, indent=2))
    for item in failed:
        print("  [FAIL] %s  %s" % (item["name"], item["detail"]))
    if live_error:
        print("  [LIVE ERROR] %s" % live_error["error"])
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
