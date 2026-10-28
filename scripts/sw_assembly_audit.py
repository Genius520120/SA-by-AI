# -*- coding: utf-8 -*-
"""装配几何自检（通用）。

用途：把"设计关系"变成机器断言，交付前跑一遍，抓出
  - 位姿偏差（组件被求解器挪走 / Fix 锁在错误位置）
  - 零件朝向（轴向件被建在错误基准面、被翻转 180°）
  - 包围盒干涉（飞轮埋进箱壁、端盖穿壳体）
  - 圆周分布越界（螺栓 PCD 四角超出端面半径）
  - 轴向尺寸链（螺栓落在缸盖下方而不是穿过它）

spec 示例见 build_spec_from_dict()。
"""
from __future__ import annotations

import json
import math
import os
import sys


def _arr(comp):
    return [float(v) for v in comp.Transform2.ArrayData]


def comp_pose(comp):
    """返回 (平移 mm, 旋转基向量 3x3 列主序, 局部Z轴世界像)。"""
    a = _arr(comp)
    t = [a[9] * 1000.0, a[10] * 1000.0, a[11] * 1000.0]
    z_axis = [a[6], a[7], a[8]]
    return t, (a[0:3], a[3:6], a[6:9]), z_axis


def comp_aabb(comp, part_size_mm, part_min_mm=(0, 0, 0)):
    """组件世界 AABB（用零件包围盒 + 组件变换推算，避开 IComponent2.GetBox 的坑）。

    part_size_mm / part_min_mm 来自零件文档的 collect_bounding_box。
    仅适用于组件无旋转或仅 90° 倍数旋转的场景；任意角旋转请用 8 角点变换。
    """
    t, R, _ = comp_pose(comp)
    xs, ys, zs = [], [], []
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                p = [part_min_mm[0] + i * part_size_mm[0],
                     part_min_mm[1] + j * part_size_mm[1],
                     part_min_mm[2] + k * part_size_mm[2]]
                w = [R[0][m] * p[0] + R[1][m] * p[1] + R[2][m] * p[2] + t[m]
                     for m in range(3)]
                xs.append(w[0]); ys.append(w[1]); zs.append(w[2])
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def check_pose(name, comp, expect_pos, tol=0.5, expect_z_axis=(0, 0, 1), axis_tol=0.1):
    t, _, z = comp_pose(comp)
    dev = max(abs(t[i] - expect_pos[i]) for i in range(3))
    ok_pos = dev <= tol
    dot = sum(z[i] * expect_z_axis[i] for i in range(3))
    ok_axis = dot >= (1.0 - axis_tol)
    return dict(name=name, kind="pose", ok=ok_pos and ok_axis,
                translation=[round(v, 2) for v in t],
                expect=[round(v, 2) for v in expect_pos],
                dev_mm=round(dev, 2), z_axis=[round(v, 2) for v in z],
                z_dot=round(dot, 3))


def check_clearance(name, box_a, box_b, axis, min_gap=0.0):
    """两个 AABB 在指定轴上的间隙（负值=重叠）。"""
    a0, a1 = box_a[0][axis], box_a[1][axis]
    b0, b1 = box_b[0][axis], box_b[1][axis]
    gap = max(b0 - a1, a0 - b1)
    return dict(name=name, kind="clearance", ok=gap >= min_gap,
                gap_mm=round(gap, 2), min_gap=min_gap,
                a_range=[round(a0, 1), round(a1, 1)],
                b_range=[round(b0, 1), round(b1, 1)])


def check_bolt_circle(name, bolt_positions, center, face_radius, hole_dia=9.0,
                      min_edge=2.0):
    """圆周分布：每颗螺栓中心到端面中心距离 + 孔半径 + 边距 ≤ 端面半径。"""
    rows = []
    ok = True
    for label, p in bolt_positions:
        d = math.hypot(p[0] - center[0], p[1] - center[1])
        edge = face_radius - d - hole_dia / 2.0
        good = edge >= min_edge
        ok = ok and good
        rows.append(dict(bolt=label, dist=round(d, 2), edge_mm=round(edge, 2), ok=good))
    return dict(name=name, kind="bolt_circle", ok=ok, center=list(center),
                face_radius=face_radius, rows=rows)


def check_axial_chain(name, box_upper, box_lower, axis=2, tol=0.5):
    """轴向尺寸链：上件底面应与下件顶面贴合（间隙≈0）。"""
    gap = box_upper[0][axis] - box_lower[1][axis]
    return dict(name=name, kind="axial_chain", ok=abs(gap) <= tol,
                gap_mm=round(gap, 2), tol=tol)


def audit(checks):
    fails = [c for c in checks if not c["ok"]]
    return dict(total=len(checks), passed=len(checks) - len(fails),
                failed=len(fails), verdict="PASS" if not fails else "FAIL",
                checks=checks)


def print_report(rep):
    print(f"=== 装配几何自检: {rep['verdict']} "
          f"({rep['passed']}/{rep['total']}) ===")
    for c in rep["checks"]:
        flag = "OK  " if c["ok"] else "FAIL"
        if c["kind"] == "pose":
            print(f"  [{flag}] {c['name']:18s} 平移={c['translation']} "
                  f"偏差={c['dev_mm']} Z轴={c['z_axis']} dot={c['z_dot']}")
        elif c["kind"] == "clearance":
            print(f"  [{flag}] {c['name']:18s} 间隙={c['gap_mm']}mm "
                  f"A={c['a_range']} B={c['b_range']}")
        elif c["kind"] == "bolt_circle":
            print(f"  [{flag}] {c['name']:18s} " +
                  " ".join(f"{r['bolt']}:d={r['dist']},edge={r['edge_mm']}"
                           for r in c["rows"]))
        elif c["kind"] == "axial_chain":
            print(f"  [{flag}] {c['name']:18s} 贴合间隙={c['gap_mm']}mm")
    return rep["verdict"] == "PASS"


if __name__ == "__main__":
    print("这是自检库，请在装配脚本里 import 使用。")
    print("示例：")
    print("  from sw_assembly_audit import *")
    print("  checks = [check_pose('flywheel', c, [-130,0,112])]")
    print("  print_report(audit(checks))")
