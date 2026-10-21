"""
建模前参数合理性检查。

用途：在动手建模之前拦截"物理量级错误"（例如节距被压到钢丝绳量级、台阶宽度远小于外径、线径根数不匹配截面积）。
每条检查返回一个记录，`severity` 为 `error` 的必须修正，为 `warn` 的需要人工确认。

用法：
    from sw_design_checks import check_params
    report = check_params({...})
    if not report["ok"]:
        for r in report["records"]:
            if not r["passed"]:
                print(r["severity"], r["name"], r["detail"])
"""
from __future__ import annotations

import math

# 默认规则（可按项目覆盖）：名称, 判据说明, 下限, 上限
RULES = {
    "lay_ratio": (15.0, 25.0, "节距/外径，导体绞合合理区间"),
    "step_ratio": (0.15, 0.60, "台阶宽/该层外径，下限保证端面可见，上限避免过长无意义外露"),
    "wall_ratio": (0.015, 0.06, "壁厚/外径"),
    "segment_gap_mm": (0.1, 0.30, "股间绝缘间隙 mm"),
    "wire_fill_ratio": (0.45, 0.62, "线束填充率（六方点阵有效区间）"),
    "exposed_lay_ratio": (0.10, 0.30, "外露长度/节距，保证绞合可见且不过度"),
}


def _record(name: str, passed: bool, severity: str, detail: str) -> dict:
    """构造单条检查记录。"""
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}


def check_params(params: dict, rules: dict | None = None) -> dict:
    """
    按比值规则检查参数。

    参数:
        params: 参数字典，单位 mm（除 ratio 外）
        rules: 可选，覆盖默认规则
    返回:
        {"ok": bool, "records": [...]}
    """
    rules = rules or RULES
    records: list[dict] = []

    def _panel(name, value, unit=""):
        lo, hi, desc = rules[name]
        ok = lo <= value <= hi
        detail = f"{desc}：{value:.3g}{unit}，应落在 [{lo:g}, {hi:g}]"
        return _record(name, ok, "error" if not ok else "ok", detail)

    # 1) 节距 / 外径
    if "lay_length_mm" in params and params["lay_length_mm"] and "outer_diameter_mm" in params:
        ratio = params["lay_length_mm"] / params["outer_diameter_mm"]
        records.append(_panel("lay_ratio", ratio, ""))

    # 2) 台阶宽（相邻层长度差）/ 外层外径
    #    i=0 是导体外露量（用 exposed_lay_ratio 单独判）；
    #    i=len-2 是最外层的"露出"（为展示内层波纹/结构而刻意退让，用宽阈值 warn）；
    #    中间各层才是真正的"层间台阶"，用 step_ratio 严格判。
    lengths = params.get("layer_lengths_mm")
    if lengths and len(lengths) >= 3:
        names = params.get("layer_names") or [f"L{i}" for i in range(len(lengths))]
        dias = params.get("layer_diameters_mm", [params.get("outer_diameter_mm")] * (len(lengths) - 1))
        for i in range(1, len(lengths) - 2):
            step = lengths[i] - lengths[i + 1]
            dia = dias[i] if i < len(dias) else params.get("outer_diameter_mm", 1.0)
            ratio = step / dia if dia else 0.0
            ok = 0.15 <= ratio <= 0.60
            records.append(_record(
                "step_ratio", ok, "error" if not ok else "ok",
                f"层 {names[i]} → {names[i+1]}：台阶 {step:.1f} mm / 外径 {dia:.1f} mm = {ratio:.2f}（应 0.15–0.60）",
            ))
        # 最外层退让（露出内层结构），只提示不判错
        reveal = lengths[-2] - lengths[-1]
        dia_last = dias[-1] if len(dias) >= len(lengths) - 1 else params.get("outer_diameter_mm", 1.0)
        ratio = reveal / dia_last if dia_last else 0.0
        records.append(_record(
            "sheath_reveal_ratio", True, "warn",
            f"最外层退让 {reveal:.1f} mm / 外径 {dia_last:.1f} mm = {ratio:.2f}"
            "（设计意图：露出内层结构；若过大请确认）",
        ))

    # 3) 壁厚 / 外径
    if "wall_thickness_mm" in params and params["wall_thickness_mm"] and "outer_diameter_mm" in params:
        ratio = params["wall_thickness_mm"] / params["outer_diameter_mm"]
        records.append(_panel("wall_ratio", ratio, ""))

    # 4) 股间间隙
    if "segment_gap_mm" in params and params["segment_gap_mm"] is not None:
        records.append(_panel("segment_gap_mm", params["segment_gap_mm"], " mm"))

    # 5) 线径 × 根数 × 紧压系数 vs 目标截面积
    need = ("wire_diameter_mm", "wire_count", "fill_factor", "section_target_mm2")
    if all(params.get(k) for k in need):
        got = math.pi / 4 * params["wire_diameter_mm"] ** 2 * params["wire_count"] * params["fill_factor"]
        err = abs(got - params["section_target_mm2"]) / params["section_target_mm2"]
        ok = err <= 0.02
        records.append(_record(
            "wire_section", ok, "error" if not ok else "ok",
            f"线束截面 {got:.1f} mm² vs 目标 {params['section_target_mm2']:.1f} mm²，偏差 {err*100:.2f}%（≤2%）",
        ))

    # 6) 线束填充率（线面积总和 / 可用面积）—— 超出上限即重叠风险
    if all(params.get(k) for k in ("wire_diameter_mm", "wire_count", "wire_area_mm2")):
        fill = (math.pi / 4 * params["wire_diameter_mm"] ** 2 * params["wire_count"]) / params["wire_area_mm2"]
        records.append(_panel("wire_fill_ratio", fill, ""))

    # 7) 导体外露长度 / 节距
    if all(params.get(k) is not None for k in ("exposed_conductor_mm", "lay_length_mm")):
        ratio = params["exposed_conductor_mm"] / params["lay_length_mm"]
        records.append(_panel("exposed_lay_ratio", ratio, ""))

    errors = [r for r in records if not r["passed"] and r["severity"] == "error"]
    return {"ok": not errors, "records": records, "error_count": len(errors)}


def format_report(report: dict) -> str:
    """把检查结果格式化为可读文本。"""
    lines = []
    for r in report["records"]:
        mark = "PASS" if r["passed"] else r["severity"].upper()
        lines.append(f"[{mark}] {r['name']}: {r['detail']}")
    lines.append(f"总计 {len(report['records'])} 项，错误 {report['error_count']} 项")
    return "\n".join(lines)
