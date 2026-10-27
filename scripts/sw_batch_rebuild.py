"""批量装配重建与验收流水线（rebuild_and_verify）。

与 `sw_assembly.rebuild_assembly` 的区别：那个按 group 布局重建、只管摆放；
本模块吃完整三轴位姿、带内存管理（>40 件必用）和机器可核的验收（固定状态 / 重开漂移 / 独立参考几何）。

把一个「零件路径 + 位姿」的清单在 SolidWorks 里重建成装配体，并做机器可核的验收：
位姿 → 全选固定 → 保存 → 重开校验 → 与独立参考几何比对包围盒。

设计要点（都是踩过坑换来的，改动前请先读 references/assembly.md）：
  - 位姿用 build_transform_data_axes 写入，绕开行/列主序歧义；
  - 每加完一个组件、设完位姿就 release_part_document，避免文档堆积把 COM 撑爆；
  - 验收基准用独立几何（如数据源自带的整机 STEP），不用数据源自报的 bbox；
  - 判定"完全定义"用 IsFixed 属性 + GetConstrainedStatus。

命令行用法：
    python sw_batch_rebuild.py job.json
    python sw_batch_rebuild.py job.json --no-reference

job.json 结构：
{
  "out_dir": "D:/work/out",
  "jobs": [
    {
      "name": "demo",
      "reference": "D:/data/demo/assembly.step",        # 可选，独立参考几何
      "parts": [
        {"part": "D:/data/demo/a.step",
         "x_axis": [1,0,0], "y_axis": [0,1,0], "z_axis": [0,0,1],
         "origin": [0,0,0]}
      ]
    }
  ]
}
单位统一为米。位姿缺省为单位阵 + 原点。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sw_assembly import (  # noqa: E402
    add_component,
    build_transform_data_axes,
    release_part_document,
)
from sw_connect import new_document, open_document  # noqa: E402
from sw_dialog_guard import DialogGuard  # noqa: E402

IDENTITY = {
    "x_axis": (1.0, 0.0, 0.0),
    "y_axis": (0.0, 1.0, 0.0),
    "z_axis": (0.0, 0.0, 1.0),
    "origin": (0.0, 0.0, 0.0),
}


def _box(doc):
    return [float(v) for v in doc.GetBox(0)]


def _max_diff_mm(a, b):
    return round(max(abs(a[i] - b[i]) for i in range(6)) * 1000, 2)


def _is_fixed(comp):
    try:
        v = getattr(comp, "IsFixed")
        return bool(v() if callable(v) else v)
    except Exception:
        return None


def rebuild_and_verify(sw, items, out_path, *, fix_all=True, reference_path=None,
                     verify_reopen=True, import_dir=None, verbose=True):
    """
    @brief 重建一个装配体并验收。
    @param sw SldWorks 应用对象。
    @param items [{"part": 路径, "x_axis": ..., "y_axis": ..., "z_axis": ..., "origin": ...}]
                 位姿缺省为单位阵 + 原点，单位米。
    @param out_path 输出 .SLDASM 的绝对路径。
    @param fix_all True 时全选后一次 FixComponent（组件全部完全定义）。
    @param reference_path 独立参考几何（如整机 STEP）；给了才算 err_vs_reference。
    @param verify_reopen True 时保存后重新打开，校验位姿不漂移。
    @param import_dir 非 .sldprt 零件先导入为 SLDPRT 并缓存在此目录；None 则用 out 目录。
    @return dict：包含 placed / fixed / err_vs_reference / reopen_drift / verdict。
    """
    import win32com.client as win32

    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    cache_dir = os.path.abspath(import_dir or out_dir)
    os.makedirs(cache_dir, exist_ok=True)
    t0 = time.time()
    rec = {"name": os.path.splitext(os.path.basename(out_path))[0], "parts": len(items)}

    asm = new_document(sw, "assembly")
    asm_title = str(asm.GetTitle)
    placed = 0
    for idx, item in enumerate(items, 1):
        src = os.path.abspath(item["part"])
        if not os.path.exists(src):
            rec.setdefault("missing", []).append(src)
            continue
        if src.lower().endswith(".sldprt"):
            sld = src
        else:
            sld = os.path.join(cache_dir, os.path.splitext(os.path.basename(src))[0] + ".sldprt")
            if not os.path.exists(sld):
                doc = open_document(sw, src, raise_on_error=True)
                if doc is None:
                    rec.setdefault("import_failed", []).append(src)
                    continue
                doc.SaveAs3(sld, 0, 0)
                sw.CloseDoc(doc.GetTitle)
                if not os.path.exists(sld):
                    rec.setdefault("import_failed", []).append(src)
                    continue

        comp = add_component(asm, sld, sw=sw)
        if comp is None:
            rec.setdefault("add_failed", []).append(sld)
            continue
        pose = {k: item.get(k, IDENTITY[k]) for k in IDENTITY}
        data = build_transform_data_axes(pose["x_axis"], pose["y_axis"], pose["z_axis"],
                                         pose["origin"])
        transform = comp.Transform2
        transform.ArrayData = win32.VARIANT(win32.pythoncom.VT_ARRAY | win32.pythoncom.VT_R8,
                                            list(data))
        if not comp.SetTransformAndSolve2(transform):
            comp.Transform2 = transform
        placed += 1
        # 关键：设完位姿立刻释放零件文档，压住 SolidWorks 常驻文档数
        release_part_document(sw, sld, keep_title=asm_title)
        if verbose and idx % 20 == 0:
            print(f"    放置 {idx}/{len(items)}", flush=True)
    rec["placed"] = placed

    try:
        asm.ForceRebuild3(False)
    except Exception:
        pass
    box_mine = _box(asm)

    if fix_all:
        t = time.time()
        asm.ClearSelection2(True)
        comps = asm.GetComponents(False)
        for c in comps:
            c.Select2(True, 0)
        asm.FixComponent()
        asm.ForceRebuild3(False)
        comps = asm.GetComponents(False)
        n_fixed = sum(1 for c in comps if _is_fixed(c) is True)
        rec["fixed"] = f"{n_fixed}/{len(comps)}"
        rec["fix_sec"] = round(time.time() - t, 1)
    else:
        rec["fixed"] = None

    asm.SaveAs3(out_path, 0, 0)
    sw.CloseDoc(asm_title)
    if not os.path.exists(out_path):
        rec["verdict"] = "FAIL"
        rec["err"] = "SLDASM 未落盘"
        rec["sec"] = round(time.time() - t0, 1)
        return rec

    if verify_reopen:
        doc = open_document(sw, out_path, raise_on_error=True)
        box2 = _box(doc)
        comps2 = doc.GetComponents(False)
        rec["fixed_reopen"] = f"{sum(1 for c in comps2 if _is_fixed(c) is True)}/{len(comps2)}"
        rec["reopen_drift"] = _max_diff_mm(box_mine, box2)
        sw.CloseDoc(doc.GetTitle)

    if reference_path and os.path.exists(reference_path):
        ref_doc = open_document(sw, os.path.abspath(reference_path), raise_on_error=True)
        rec["err_vs_reference"] = _max_diff_mm(box_mine, _box(ref_doc))
        sw.CloseDoc(ref_doc.GetTitle)

    ok = True
    if rec.get("err_vs_reference") is not None:
        ok = ok and rec["err_vs_reference"] <= 5.0
    if rec.get("fixed"):
        n, d = rec["fixed"].split("/")
        ok = ok and n == d
    if rec.get("reopen_drift") is not None:
        ok = ok and rec["reopen_drift"] <= 0.5
    rec["verdict"] = "PASS" if ok else "REVIEW"
    rec["sec"] = round(time.time() - t0, 1)
    return rec


def run_jobs(sw, spec, use_reference=True, verbose=True):
    """批量跑 job 清单，单台异常不影响后续。"""
    out_dir = os.path.abspath(spec.get("out_dir", "."))
    results = []
    for job in spec.get("jobs", []):
        name = job["name"]
        out_path = os.path.join(out_dir, name + ".SLDASM")
        ref = job.get("reference") if use_reference else None
        try:
            with DialogGuard(interval=0.4, verbose=verbose):
                rec = rebuild_and_verify(sw, job["parts"], out_path,
                                       reference_path=ref, verbose=verbose)
        except Exception as exc:
            rec = {"name": name, "verdict": "FAIL", "err": repr(exc)[:160]}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
    return results


def main():
    ap = argparse.ArgumentParser(description="批量装配重建与验收")
    ap.add_argument("job_json", help="job 清单 JSON")
    ap.add_argument("--no-reference", action="store_true", help="跳过独立参考几何比对")
    ap.add_argument("--report", help="结果另存为 JSON")
    args = ap.parse_args()

    import win32com.client as win32
    sw = win32.Dispatch("SldWorks.Application")
    sw.Visible = True
    spec = json.load(open(args.job_json, encoding="utf-8"))
    results = run_jobs(sw, spec, use_reference=not args.no_reference)

    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    print(f"\n合计 {len(results)} 台，PASS {passed}，REVIEW/FAIL {len(results) - passed}")
    if args.report:
        json.dump(results, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("报告:", args.report)


if __name__ == "__main__":
    main()
