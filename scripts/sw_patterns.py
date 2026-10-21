"""
通用几何排布模式：与具体零件无关的点阵 / 间距计算。

当前提供六方点阵（hexagonal lattice）——用于圆线束、螺栓圆周分布、散热孔阵列等
任何"在平面区域内铺等距点"的场景。相切关系由构造保证（点距 = spacing），
不依赖逐行角度推算（后者在区域半径变化处必然碰撞，实测踩坑）。

用法：
    from sw_patterns import hex_lattice_2d, min_pair_distance

    pts = hex_lattice_2d(spacing=5.5, predicate=lambda x, y: 10 <= math.hypot(x, y) <= 30)
    if min_pair_distance(pts) < spacing - 1e-6:
        raise RuntimeError("存在重叠")
"""
from __future__ import annotations

import math


def hex_lattice_2d(spacing: float, predicate, bounds=None) -> list[tuple[float, float]]:
    """
    生成点距恰为 `spacing` 的六方点阵，保留满足谓词的点。

    参数:
        spacing: 相邻点中心距。相邻点**恰好相切**（不重叠）。
        predicate: callable(x, y) -> bool，为 True 的点保留。
                   用它来表达任意区域（圆环、扇形、多边形……），
                   本模块不假设区域形状。
        bounds: 可选 (xmin, xmax, ymin, ymax) 限定扫描范围；
                不给时按 ±(8 × spacing) 扫描，通常够用。
    返回:
        [(x, y), ...]
    """
    if spacing <= 0:
        raise ValueError(f"spacing 必须为正数，收到 {spacing}")
    dy = spacing * math.sqrt(3) / 2.0
    n = 8 if bounds is None else int(max(abs(bounds[0]), abs(bounds[1]),
                                         abs(bounds[2]), abs(bounds[3])) / spacing) + 2
    pts: list[tuple[float, float]] = []
    for j in range(-n, n + 1):
        y = j * dy
        xoff = (spacing / 2.0) if (j % 2) else 0.0
        for i in range(-n, n + 1):
            x = i * spacing + xoff
            if bounds is not None:
                if not (bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3]):
                    continue
            if predicate is None or predicate(x, y):
                pts.append((x, y))
    return pts


def min_pair_distance(points) -> float:
    """
    点集中任意两点的最小距离（O(n²)）。

    用途：验证排布不重叠——若最小距离 ≥ 预期间距，则所有点两两不相交。
    """
    pts = list(points)
    if len(pts) < 2:
        return float("inf")
    worst = float("inf")
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1:]:
            worst = min(worst, math.hypot(x2 - x1, y2 - y1))
    return worst


def tangent_pair_count(points, spacing: float, tol: float = 1e-6) -> int:
    """统计恰好相切（中心距 = spacing ± tol）的配对数，用于验收报告。"""
    pts = list(points)
    count = 0
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1:]:
            if abs(math.hypot(x2 - x1, y2 - y1) - spacing) <= tol:
                count += 1
    return count
