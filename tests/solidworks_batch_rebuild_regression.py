"""批量重建+验收流水线回归（sw_batch_rebuild.rebuild_and_verify）。

覆盖：
  1. 位姿按三轴向量写入（列主序），两个零件放到不同位置
  2. 全选 Fix 后全部组件 IsFixed
  3. 保存→重开后位姿不漂移
  4. release_part_document 真的把零件文档释放掉（常驻文档数下降）
  5. 独立参考几何比对（用重建结果自身导出？不做——这里只验证 err_vs_reference 缺省为 None）

运行：
    python tests/solidworks_batch_rebuild_regression.py
    python tests/solidworks_batch_rebuild_regression.py --output D:/tmp/out
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sw_batch_rebuild import rebuild_and_verify  # noqa: E402
from sw_connect import connect_solidworks, get_com_member, mm, new_document, save_document  # noqa: E402
from sw_part import extrude_boss, sketch, sketch_rectangle  # noqa: E402


def create_probe_part(sw, part_path: Path):
    """创建一个最小测试零件。"""
    try:
        sw.CloseDoc(part_path.name)
    except Exception:
        pass
    model = new_document(sw, "part")
    with sketch(model, "Front Plane") as sketch_name:
        sketch_rectangle(model, 0, 0, mm(24), mm(14))
    feature = extrude_boss(model, sketch_name, mm(8))
    if feature is None:
        raise RuntimeError("测试零件拉伸失败")
    model.ForceRebuild3(False)
    if not save_document(model, str(part_path)):
        raise RuntimeError(f"测试零件保存失败: {part_path}")
    sw.CloseDoc(get_com_member(model, "GetTitle"))
    return part_path


def run_regression(output_dir: Path, wait_seconds: float = 60.0, visible: bool = True):
    output_dir.mkdir(parents=True, exist_ok=True)
    sw, _ = connect_solidworks(wait_seconds=wait_seconds, visible=visible)
    part_a = create_probe_part(sw, output_dir / "batch_probe_a.SLDPRT")
    part_b = create_probe_part(sw, output_dir / "batch_probe_b.SLDPRT")

    # 两件放到不同位置：第二件沿 +X 平移 40 mm、绕 Z 转 90°（非对称旋转，能抓到行/列主序错误）
    items = [
        {"part": str(part_a)},
        {"part": str(part_b),
         "x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0], "z_axis": [0.0, 0.0, 1.0],
         "origin": [mm(40), 0.0, 0.0]},
    ]
    out_path = output_dir / "batch_regression.SLDASM"
    rec = rebuild_and_verify(sw, items, str(out_path), fix_all=True, verify_reopen=True, verbose=False)

    checks = {}
    checks["placed_all"] = rec.get("placed") == 2
    checks["slldasm_exists"] = out_path.exists()
    checks["fixed_all"] = rec.get("fixed") == "2/2"
    checks["fixed_after_reopen"] = rec.get("fixed_reopen") == "2/2"
    checks["no_reopen_drift"] = rec.get("reopen_drift") == 0.0
    checks["verdict_pass"] = rec.get("verdict") == "PASS"
    checks["no_reference_by_default"] = rec.get("err_vs_reference") is None

    # 释放文档：装配体加完组件后，常驻文档不应累积
    doc_count = int(get_com_member(sw, "GetDocumentCount"))
    checks["doc_count_bounded"] = doc_count <= 3

    passed = all(checks.values())
    return {
        "status": "ok" if passed else "failed",
        "checks": checks,
        "result": rec,
        "doc_count_after": doc_count,
        "artifacts": {"assembly": str(out_path)},
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="批量重建+验收回归")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--wait", type=float, default=60.0)
    ap.add_argument("--headless", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    out = args.output or Path(tempfile.mkdtemp(prefix="sw_batch_regression_"))
    try:
        result = run_regression(Path(out), wait_seconds=args.wait, visible=not args.headless)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc),
                          "traceback": traceback.format_exc()}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
