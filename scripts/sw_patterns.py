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


def _centroid_offset(pts) -> float:
    """点集质心到原点的距离。"""
    if not pts:
        return float("inf")
    n = len(pts)
    return math.hypot(sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def hex_lattice_2d(spacing: float, predicate, bounds=None,
                   balance: bool = True, balance_steps: int = 8) -> list[tuple[float, float]]:
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

    def _generate(ox: float, oy: float) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for j in range(-n, n + 1):
            y = j * dy + oy
            xoff = (spacing / 2.0) if (j % 2) else 0.0
            for i in range(-n, n + 1):
                x = i * spacing + xoff + ox
                if bounds is not None:
                    if not (bounds[0] <= x <= bounds[1] and bounds[2] <= y <= bounds[3]):
                        continue
                if predicate is None or predicate(x, y):
                    pts.append((x, y))
        return pts

    if not balance:
        return _generate(0.0, 0.0)

    # 两级配平（都保持点间距离不变，即相切/不重叠关系不变）：
    #   1) 锚点寻优：点阵(6次对称)与区域(任意对称)不匹配时，裁剪后的点集质心会偏离原点，
    #      在一个 spacing 内扫描锚点偏移，取质心最靠近原点的一组。
    #   2) 质心校正：整体平移 -质心，把残余偏移消到 0；平移后逐点复检谓词，
    #      有点出界则放弃校正、保留锚点寻优结果。
    best, best_off = None, None
    step = spacing / max(1, balance_steps)
    for a in range(balance_steps):
        for b in range(balance_steps):
            cand = _generate(a * step, b * step)
            if not cand:
                continue
            off = _centroid_offset(cand)
            if best is None or off < best_off:
                best, best_off = cand, off
    if best is None:
        raise RuntimeError("六方点阵寻优失败：没有任何锚点能产生有效点集")

    n = len(best)
    cx = sum(p[0] for p in best) / n
    cy = sum(p[1] for p in best) / n
    if best_off > 1e-9 and predicate is not None:
        shifted = [(x - cx, y - cy) for x, y in best]
        if all(predicate(x, y) for x, y in shifted):
            return shifted
    return best


def rotational_copies(points, count: int, angle_deg: float) -> list[tuple[float, float]]:
    """
    把一组点绕原点旋转复制 count 份（每份间隔 angle_deg）。

    用途：N 等分对称结构（辐条、扇区填充、螺栓圆周分布）。整体复制保证
    **严格的 N 次旋转对称**——合成点集的质心精确落在原点上，且点间距离
    关系与单份内部完全一致（相切/不重叠不会因复制而破坏）。
    """
    out: list[tuple[float, float]] = []
    for k in range(count):
        a = math.radians(angle_deg) * k
        c, s = math.cos(a), math.sin(a)
        out.extend((x * c - y * s, x * s + y * c) for x, y in points)
    return out


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
