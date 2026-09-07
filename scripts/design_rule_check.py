"""
@file design_rule_check.py
@brief 设计规则检查（DRC）引擎与标准规则集——面向设计审计的通用检查器。

定位：制造性由 dfm_review.py 负责；本模块是**设计意图层**的规则检查器，把几何健全性、
孔位规则、标准件（齿轮/弹簧/链轮）合理性、装配约束和工程图完整性等规则跑在一个
NeutralCadDocument 上，按严重度聚合成一份可审计报告。既有 DFM/几何/图纸审查可作为证据源，
本模块不重复实现它们。

设计哲学（与本仓库一致）：
- 规则逻辑是**纯函数**，消费中性文档，可离线单测；
- 规则集通过**声明式 Profile**（design_rule_profiles.py）配置与补充，代理可把自然语言
  翻译成 Profile JSON，无需写代码；
- 从活动 SolidWorks 模型抽取快照为 `pilot`，需真机验证；
- 报告始终 `reviewRequired=true`，不作为无人值守放行依据。

用法：
    report = build_drc_report("part.cadstudio.json", profiles=[profile_dict])
    write_drc_report("part.cadstudio.json", "out/drc.json", profiles=[...])
    rules = list_rules()  # 供代理了解可配置的内置规则
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .design_rule_profiles import DrcProfileError, load_profile, merge_profiles
except ImportError:
    from design_rule_profiles import DrcProfileError, load_profile, merge_profiles

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "minWallThicknessMm": 1.0,
    "minFeatureSizeMm": 0.5,
    "holeEdgeDistanceRatio": 1.0,       # 孔边到零件边 >= ratio * 半径
    "holeEdgeDistanceMinMm": 1.5,
    "holeSpacingRatio": 2.0,            # 相邻孔中心距 >= ratio * 大孔半径
    "threadEngagementRatio": 1.0,       # 螺纹啮合深度 >= ratio * 公称直径
    "gearMinTeeth": 17,                 # 20° 标准全齿避免根切的最小齿数
    "sprocketMinTeeth": 17,
    "springIndexMin": 4.0,
    "springIndexMax": 12.0,
    "springIndexHardMin": 3.0,
    "springIndexHardMax": 16.0,
}

_SEVERITY_TO_STATUS = {"critical": "fail", "major": "fail", "warning": "warning", "minor": "warning", "info": "info"}


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _check(check_id: str, category: str, status: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    """@brief 构造稳定检查项。"""
    payload: dict[str, Any] = {
        "id": check_id,
        "category": category,
        "status": status,
        "severity": severity,
        "message": message,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


# ---------------------------------------------------------------------------
# 中性文档解析
# ---------------------------------------------------------------------------


def _load_document(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        document = dict(source)
    else:
        document = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("NeutralCadDocument 必须是 JSON 对象")
    if not str(document.get("documentId") or "").strip():
        raise ValueError("NeutralCadDocument 缺少 documentId")
    return document


def _extract_context(document: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 从中性文档抽取 DRC 需要的规范化上下文（长度统一 mm）。"""
    units = str(document.get("units") or "mm").strip().lower()
    factor = UNIT_TO_MM.get(units)
    if factor is None:
        raise ValueError(f"DRC 不支持单位 {units}")
    metadata = document.get("metadata") if isinstance(document.get("metadata"), Mapping) else {}
    design = metadata.get("design") if isinstance(metadata.get("design"), Mapping) else {}
    manufacturing = metadata.get("manufacturing") if isinstance(metadata.get("manufacturing"), Mapping) else {}
    assembly = metadata.get("assembly") if isinstance(metadata.get("assembly"), Mapping) else {}
    drawing = metadata.get("drawing") if isinstance(metadata.get("drawing"), Mapping) else {}

    features = [f for f in document.get("features", []) if isinstance(f, Mapping)]
    holes: list[dict[str, Any]] = []
    for feature in features:
        if str(feature.get("type") or "").lower() != "hole":
            continue
        params = feature.get("parameters") if isinstance(feature.get("parameters"), Mapping) else {}
        diameter = _num(params.get("diameter"))
        if diameter is None and _num(params.get("radius")) is not None:
            diameter = _num(params.get("radius")) * 2.0
        holes.append(
            {
                "id": str(feature.get("id") or ""),
                "diameter_mm": diameter * factor if diameter is not None else None,
                "depth_mm": _num(params.get("depth")) * factor if _num(params.get("depth")) is not None else None,
                "edge_distance_mm": _num(params.get("edgeDistance")) * factor
                if _num(params.get("edgeDistance")) is not None
                else None,
                "engagement_mm": _num(params.get("threadEngagement")) * factor
                if _num(params.get("threadEngagement")) is not None
                else None,
                "thread": str(params.get("thread") or "") or None,
                "thread_nominal_mm": _num(params.get("threadNominal")) * factor
                if _num(params.get("threadNominal")) is not None
                else None,
                "x_mm": _num(params.get("x")) * factor if _num(params.get("x")) is not None else None,
                "y_mm": _num(params.get("y")) * factor if _num(params.get("y")) is not None else None,
            }
        )

    min_wall = _num(design.get("minWallThicknessMm"))
    if min_wall is None:
        raw_wall = _num(manufacturing.get("wallThickness"))
        min_wall = raw_wall * factor if raw_wall is not None else None
    else:
        min_wall = min_wall  # design 块默认已是 mm

    standard_parts = document.get("standardParts")
    if not isinstance(standard_parts, list):
        standard_parts = metadata.get("standardParts") if isinstance(metadata.get("standardParts"), list) else []
    standard_parts = [p for p in standard_parts if isinstance(p, Mapping)]

    return {
        "documentId": str(document.get("documentId")),
        "units": units,
        "factor": factor,
        "features": features,
        "holes": holes,
        "min_wall_mm": min_wall,
        "standard_parts": standard_parts,
        "assembly": dict(assembly),
        "drawing": dict(drawing),
    }


# ---------------------------------------------------------------------------
# 内置标准规则
# ---------------------------------------------------------------------------


def rule_geometry(ctx: Mapping[str, Any], th: Mapping[str, float]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    min_wall = ctx.get("min_wall_mm")
    limit = th["minWallThicknessMm"]
    if min_wall is None:
        checks.append(_check("DRC-GEO-001", "geometry", "info", "info", "未提供最小壁厚，跳过壁厚检查", threshold_mm=limit))
    elif min_wall < limit:
        checks.append(
            _check("DRC-GEO-001", "geometry", "fail", "major", f"最小壁厚 {min_wall:g}mm 小于阈值 {limit:g}mm", value_mm=min_wall, threshold_mm=limit)
        )
    elif min_wall < limit * 1.5:
        checks.append(
            _check("DRC-GEO-001", "geometry", "warning", "minor", f"最小壁厚 {min_wall:g}mm 接近阈值 {limit:g}mm", value_mm=min_wall, threshold_mm=limit)
        )
    else:
        checks.append(_check("DRC-GEO-001", "geometry", "pass", "info", f"最小壁厚 {min_wall:g}mm 合格", value_mm=min_wall, threshold_mm=limit))
    return checks


def rule_holes(ctx: Mapping[str, Any], th: Mapping[str, float]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    holes = ctx.get("holes", [])
    if not holes:
        return [_check("DRC-HOLE-000", "holes", "info", "info", "文档无孔特征，跳过孔规则")]
    for hole in holes:
        hid = hole.get("id") or "hole"
        diameter = hole.get("diameter_mm")
        if diameter is not None and diameter <= 0:
            checks.append(_check("DRC-HOLE-004", "holes", "fail", "critical", f"孔 {hid} 直径非正", target=hid))
            continue
        # 孔边距
        edge = hole.get("edge_distance_mm")
        if edge is not None and diameter is not None:
            required = max(th["holeEdgeDistanceMinMm"], th["holeEdgeDistanceRatio"] * diameter / 2.0)
            if edge < required:
                checks.append(
                    _check("DRC-HOLE-001", "holes", "fail", "major", f"孔 {hid} 边距 {edge:g}mm 小于要求 {required:g}mm", target=hid, value_mm=edge, required_mm=required)
                )
        # 螺纹啮合深度
        engagement = hole.get("engagement_mm")
        nominal = hole.get("thread_nominal_mm")
        if hole.get("thread") and engagement is not None and nominal:
            required = th["threadEngagementRatio"] * nominal
            if engagement < required:
                checks.append(
                    _check("DRC-HOLE-003", "holes", "warning", "warning", f"孔 {hid} 螺纹啮合 {engagement:g}mm 小于建议 {required:g}mm", target=hid, value_mm=engagement, required_mm=required)
                )
    # 孔间距
    positioned = [h for h in holes if h.get("x_mm") is not None and h.get("y_mm") is not None and h.get("diameter_mm")]
    for i in range(len(positioned)):
        for j in range(i + 1, len(positioned)):
            a, b = positioned[i], positioned[j]
            distance = ((a["x_mm"] - b["x_mm"]) ** 2 + (a["y_mm"] - b["y_mm"]) ** 2) ** 0.5
            required = th["holeSpacingRatio"] * max(a["diameter_mm"], b["diameter_mm"]) / 2.0
            if distance < required:
                checks.append(
                    _check("DRC-HOLE-002", "holes", "warning", "warning", f"孔 {a['id']} 与 {b['id']} 中心距 {distance:g}mm 小于建议 {required:g}mm", value_mm=distance, required_mm=required)
                )
    if not checks:
        checks.append(_check("DRC-HOLE-OK", "holes", "pass", "info", f"{len(holes)} 个孔通过边距/间距/啮合检查"))
    return checks


def rule_standard_parts(ctx: Mapping[str, Any], th: Mapping[str, float]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    parts = ctx.get("standard_parts", [])
    if not parts:
        return [_check("DRC-STD-000", "standard_parts", "info", "info", "文档无标准件，跳过标准件规则")]
    for part in parts:
        ptype = str(part.get("type") or "").lower()
        pid = str(part.get("id") or ptype or "part")
        if ptype in {"spur_gear", "gear"}:
            if part.get("is_pointed") is True:
                checks.append(_check("DRC-STD-001", "standard_parts", "fail", "major", f"齿轮 {pid} 齿顶尖化（顶隙为 0）", target=pid))
            teeth = _num(part.get("teeth"))
            shift = _num(part.get("profile_shift_coeff")) or 0.0
            if teeth is not None and teeth < th["gearMinTeeth"] and shift <= 0.0:
                checks.append(_check("DRC-STD-002", "standard_parts", "warning", "warning", f"齿轮 {pid} 齿数 {teeth:g} 低于 {th['gearMinTeeth']:g} 且无正变位，存在根切风险", target=pid))
        elif ptype in {"compression_spring", "spring"}:
            index = _num(part.get("spring_index"))
            if index is not None:
                if index < th["springIndexHardMin"] or index > th["springIndexHardMax"]:
                    checks.append(_check("DRC-STD-003", "standard_parts", "fail", "major", f"弹簧 {pid} 指数 C={index:g} 超出可制造范围 [{th['springIndexHardMin']:g}, {th['springIndexHardMax']:g}]", target=pid))
                elif index < th["springIndexMin"] or index > th["springIndexMax"]:
                    checks.append(_check("DRC-STD-003", "standard_parts", "warning", "warning", f"弹簧 {pid} 指数 C={index:g} 超出推荐范围 [{th['springIndexMin']:g}, {th['springIndexMax']:g}]", target=pid))
            solid = _num(part.get("solid_length_mm"))
            free = _num(part.get("free_length_mm"))
            if solid is not None and free is not None and solid >= free:
                checks.append(_check("DRC-STD-004", "standard_parts", "fail", "critical", f"弹簧 {pid} 实体长度 {solid:g}mm 不小于自由长度 {free:g}mm", target=pid))
        elif ptype in {"roller_sprocket", "sprocket"}:
            teeth = _num(part.get("teeth"))
            if teeth is not None and teeth < th["sprocketMinTeeth"]:
                checks.append(_check("DRC-STD-005", "standard_parts", "warning", "warning", f"链轮 {pid} 齿数 {teeth:g} 低于 {th['sprocketMinTeeth']:g}，磨损/多边形效应风险", target=pid))
    if not checks:
        checks.append(_check("DRC-STD-OK", "standard_parts", "pass", "info", f"{len(parts)} 个标准件通过合理性检查"))
    return checks


def rule_assembly(ctx: Mapping[str, Any], th: Mapping[str, float]) -> list[dict[str, Any]]:
    assembly = ctx.get("assembly", {})
    if not assembly:
        return [_check("DRC-ASM-000", "assembly", "info", "info", "无装配元数据，跳过装配规则")]
    checks: list[dict[str, Any]] = []
    interferences = assembly.get("interferences")
    count = len(interferences) if isinstance(interferences, list) else _num(assembly.get("interferenceCount"))
    if count and count > 0:
        checks.append(_check("DRC-ASM-001", "assembly", "fail", "critical", f"检测到 {int(count)} 处装配干涉", value=int(count)))
    components = _num(assembly.get("componentCount"))
    mates = _num(assembly.get("mateCount"))
    if components is not None and mates is not None and components >= 2 and mates == 0:
        checks.append(_check("DRC-ASM-002", "assembly", "warning", "warning", f"{int(components)} 个组件但无配合，装配欠约束", value=int(components)))
    if not checks:
        checks.append(_check("DRC-ASM-OK", "assembly", "pass", "info", "装配约束与干涉检查通过"))
    return checks


def rule_drawing(ctx: Mapping[str, Any], th: Mapping[str, float]) -> list[dict[str, Any]]:
    drawing = ctx.get("drawing", {})
    if not drawing:
        return [_check("DRC-DRW-000", "drawing", "info", "info", "无工程图元数据，跳过图纸规则")]
    checks: list[dict[str, Any]] = []
    missing = drawing.get("requiredDimensionsMissing")
    if isinstance(missing, list) and missing:
        checks.append(_check("DRC-DRW-001", "drawing", "fail", "major", f"工程图缺少必需尺寸: {', '.join(str(m) for m in missing[:8])}", value=len(missing)))
    without_tol = _num(drawing.get("dimensionsWithoutTolerance"))
    if without_tol is not None and without_tol > 0:
        checks.append(_check("DRC-DRW-002", "drawing", "warning", "minor", f"{int(without_tol)} 个尺寸缺少公差标注", value=int(without_tol)))
    if not checks:
        checks.append(_check("DRC-DRW-OK", "drawing", "pass", "info", "工程图完整性检查通过"))
    return checks


BUILTIN_RULES: list[Callable[[Mapping[str, Any], Mapping[str, float]], list[dict[str, Any]]]] = [
    rule_geometry,
    rule_holes,
    rule_standard_parts,
    rule_assembly,
    rule_drawing,
]

RULE_CATALOG = [
    {"id": "DRC-GEO-001", "category": "geometry", "title": "最小壁厚", "thresholds": ["minWallThicknessMm"], "severity": "major"},
    {"id": "DRC-HOLE-001", "category": "holes", "title": "孔到边缘距离", "thresholds": ["holeEdgeDistanceRatio", "holeEdgeDistanceMinMm"], "severity": "major"},
    {"id": "DRC-HOLE-002", "category": "holes", "title": "孔间距", "thresholds": ["holeSpacingRatio"], "severity": "warning"},
    {"id": "DRC-HOLE-003", "category": "holes", "title": "螺纹啮合深度", "thresholds": ["threadEngagementRatio"], "severity": "warning"},
    {"id": "DRC-HOLE-004", "category": "holes", "title": "孔尺寸有效性", "thresholds": [], "severity": "critical"},
    {"id": "DRC-STD-001", "category": "standard_parts", "title": "齿轮尖齿", "thresholds": [], "severity": "major"},
    {"id": "DRC-STD-002", "category": "standard_parts", "title": "齿轮根切齿数", "thresholds": ["gearMinTeeth"], "severity": "warning"},
    {"id": "DRC-STD-003", "category": "standard_parts", "title": "弹簧指数范围", "thresholds": ["springIndexMin", "springIndexMax", "springIndexHardMin", "springIndexHardMax"], "severity": "major"},
    {"id": "DRC-STD-004", "category": "standard_parts", "title": "弹簧实体/自由长度", "thresholds": [], "severity": "critical"},
    {"id": "DRC-STD-005", "category": "standard_parts", "title": "链轮最小齿数", "thresholds": ["sprocketMinTeeth"], "severity": "warning"},
    {"id": "DRC-ASM-001", "category": "assembly", "title": "装配干涉", "thresholds": [], "severity": "critical"},
    {"id": "DRC-ASM-002", "category": "assembly", "title": "装配欠约束", "thresholds": [], "severity": "warning"},
    {"id": "DRC-DRW-001", "category": "drawing", "title": "缺必需尺寸", "thresholds": [], "severity": "major"},
    {"id": "DRC-DRW-002", "category": "drawing", "title": "尺寸缺公差", "thresholds": [], "severity": "minor"},
]


def list_rules() -> dict[str, Any]:
    """@brief 返回内置规则目录与默认阈值，供代理据此用自然语言配置 Profile。"""
    return {
        "schema": "cadstudio.drc-rule-catalog",
        "version": "1.0",
        "rules": [dict(rule) for rule in RULE_CATALOG],
        "defaultThresholds": dict(DEFAULT_THRESHOLDS),
        "customRule": {
            "categories": sorted({"geometry", "holes", "standard_parts", "assembly", "drawing", "custom"}),
            "severities": ["info", "minor", "warning", "major", "critical"],
            "operators": ["lt", "lte", "gt", "gte", "eq", "ne"],
            "appliesTo": ["hole", "feature", "standard_part", "document"],
        },
    }


# ---------------------------------------------------------------------------
# 声明式自定义规则
# ---------------------------------------------------------------------------


_HOLE_FIELD_ALIASES = {"diameter": "diameter_mm", "depth": "depth_mm", "edgeDistance": "edge_distance_mm", "engagement": "engagement_mm"}


def _document_scalars(ctx: Mapping[str, Any]) -> dict[str, Any]:
    assembly = ctx.get("assembly", {})
    interferences = assembly.get("interferences")
    return {
        "minWallThicknessMm": ctx.get("min_wall_mm"),
        "holeCount": len(ctx.get("holes", [])),
        "standardPartCount": len(ctx.get("standard_parts", [])),
        "componentCount": _num(assembly.get("componentCount")),
        "mateCount": _num(assembly.get("mateCount")),
        "interferenceCount": len(interferences) if isinstance(interferences, list) else _num(assembly.get("interferenceCount")),
    }


def _compare(left: Any, operator: str, right: Any) -> bool:
    left_num, right_num = _num(left), _num(right if not isinstance(right, str) else None)
    if isinstance(right, str) and not isinstance(left, (int, float)):
        left_s, right_s = str(left), right
        if operator == "eq":
            return left_s == right_s
        if operator == "ne":
            return left_s != right_s
        return False
    if left_num is None or right_num is None:
        return False
    return {
        "lt": left_num < right_num,
        "lte": left_num <= right_num,
        "gt": left_num > right_num,
        "gte": left_num >= right_num,
        "eq": left_num == right_num,
        "ne": left_num != right_num,
    }[operator]


def _apply_custom_rule(ctx: Mapping[str, Any], rule: Mapping[str, Any]) -> list[dict[str, Any]]:
    applies = rule["appliesTo"]
    field = rule["field"]
    status = _SEVERITY_TO_STATUS[rule["severity"]]
    checks: list[dict[str, Any]] = []

    def emit(target: str, actual: Any) -> None:
        checks.append(
            _check(rule["id"], rule.get("category", "custom"), status, rule["severity"], rule["message"], target=target, field=field, actual=actual, operator=rule["operator"], value=rule["value"])
        )

    if applies == "hole":
        key = _HOLE_FIELD_ALIASES.get(field, field)
        for hole in ctx.get("holes", []):
            actual = hole.get(key)
            if actual is not None and _compare(actual, rule["operator"], rule["value"]):
                emit(hole.get("id") or "hole", actual)
    elif applies == "feature":
        for feature in ctx.get("features", []):
            params = feature.get("parameters") if isinstance(feature.get("parameters"), Mapping) else {}
            actual = params.get(field)
            if actual is not None and _compare(actual, rule["operator"], rule["value"]):
                emit(str(feature.get("id") or "feature"), actual)
    elif applies == "standard_part":
        for part in ctx.get("standard_parts", []):
            actual = part.get(field)
            if actual is not None and _compare(actual, rule["operator"], rule["value"]):
                emit(str(part.get("id") or part.get("type") or "part"), actual)
    else:  # document
        actual = _document_scalars(ctx).get(field)
        if actual is not None and _compare(actual, rule["operator"], rule["value"]):
            emit(ctx.get("documentId", "document"), actual)

    if not checks:
        checks.append(_check(rule["id"], rule.get("category", "custom"), "pass", "info", f"自定义规则 {rule['id']} 未发现违规"))
    return checks


# ---------------------------------------------------------------------------
# 报告构建
# ---------------------------------------------------------------------------


def _resolve_profile(profiles: Sequence[str | Path | Mapping[str, Any]] | None) -> dict[str, Any]:
    if not profiles:
        return {"schema": "cadstudio.drc-profile", "version": "1.0", "id": "default", "thresholds": {}, "disabledRules": [], "customRules": []}
    return merge_profiles([load_profile(item) for item in profiles])


def _aggregate_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {check.get("status") for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warning" in statuses:
        return "warning"
    return "pass"


def build_drc_report(
    source: str | Path | Mapping[str, Any],
    *,
    profiles: Sequence[str | Path | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """@brief 在中性文档上运行 DRC 并返回报告（不写盘）。"""
    try:
        document = _load_document(source)
        context = _extract_context(document)
        profile = _resolve_profile(profiles)
    except (ValueError, DrcProfileError) as exc:
        return {
            "schema": "cadstudio.drc-report",
            "version": "1.0",
            "status": "blocked",
            "reviewRequired": True,
            "checks": [_check("DRC-INPUT", "input", "fail", "critical", str(exc))],
            "summary": {"fail": 1, "warning": 0, "pass": 0, "info": 0, "total": 1},
            "generatedAt": _now_iso(),
        }

    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(profile.get("thresholds", {}))
    disabled = set(profile.get("disabledRules", []))

    checks: list[dict[str, Any]] = []
    for rule_fn in BUILTIN_RULES:
        for check in rule_fn(context, thresholds):
            if check["id"] in disabled:
                continue
            checks.append(check)
    for custom_rule in profile.get("customRules", []):
        if custom_rule["id"] in disabled:
            continue
        checks.extend(_apply_custom_rule(context, custom_rule))

    summary = {
        "fail": sum(1 for c in checks if c["status"] == "fail"),
        "warning": sum(1 for c in checks if c["status"] == "warning"),
        "pass": sum(1 for c in checks if c["status"] == "pass"),
        "info": sum(1 for c in checks if c["status"] == "info"),
        "total": len(checks),
    }
    return {
        "schema": "cadstudio.drc-report",
        "version": "1.0",
        "documentId": context["documentId"],
        "units": "mm",
        "status": _aggregate_status(checks),
        "reviewRequired": True,
        "summary": summary,
        "checks": checks,
        "profileApplied": {
            "id": profile.get("id", "default"),
            "thresholds": thresholds,
            "disabledRules": sorted(disabled),
            "customRuleCount": len(profile.get("customRules", [])),
        },
        "limitations": [
            "DRC 只审查中性文档中已提供的证据；缺失的截面按 info 跳过，不代表合格",
            "报告始终 reviewRequired=true，不得作为无人值守放行依据",
            "从活动 SolidWorks 模型抽取快照为 pilot，需真机与工程复核",
        ],
        "generatedAt": _now_iso(),
    }


def _versioned_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}.v{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def write_drc_report(
    source: str | Path | Mapping[str, Any],
    output_path: str | Path,
    *,
    profiles: Sequence[str | Path | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """@brief 运行 DRC 并写出版本化报告，返回值附带 SHA-256 产物证据。"""
    report = build_drc_report(source, profiles=profiles)
    target = _versioned_target(Path(output_path).expanduser().resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["reportPath"] = str(target)
    report["artifacts"] = [
        {
            "kind": "drc_report",
            "type": "artifact",
            "format": "json",
            "path": str(target),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "sizeBytes": target.stat().st_size,
            "producedThisRun": True,
        }
    ]
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """@brief 命令行入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="对 NeutralCadDocument 运行设计规则检查（DRC）。")
    parser.add_argument("input", nargs="?", help="NeutralCadDocument (.cadstudio.json) 路径。")
    parser.add_argument("--output", help="DRC 报告 JSON 输出路径。")
    parser.add_argument("--profile", action="append", default=[], help="DRC Profile JSON 路径，可多次传入。")
    parser.add_argument("--list-rules", action="store_true", help="打印内置规则目录与默认阈值后退出。")
    args = parser.parse_args(argv)

    if args.list_rules:
        print(json.dumps(list_rules(), ensure_ascii=False, indent=2))
        return 0
    if not args.input:
        parser.error("需要提供 NeutralCadDocument 路径，或使用 --list-rules")
    if args.output:
        report = write_drc_report(args.input, args.output, profiles=args.profile or None)
    else:
        report = build_drc_report(args.input, profiles=args.profile or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("status") in {"blocked", "fail"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
