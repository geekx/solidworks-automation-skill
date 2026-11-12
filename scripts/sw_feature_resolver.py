"""Feature Resolver：把「选中的面/边」提升为「可编辑的特征与尺寸」。

解析顺序（`docs/point-and-prompt/roadmap.md` 3.2）：

    Selected Face / Edge
        → Owning Body / Adjacent Geometry
        → Candidate Feature(s)
        → Parent Sketch / Feature Parameters
        → Editable Design Intent

解析结果永远是候选列表 + 置信度 + 方法证据，不强行返回唯一特征；导入实体、
镜像/阵列、派生零件等追溯不到时返回 ``unresolved``，由上层提示用户改用显式
特征操作，禁止伪造归属。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: 面几何 → 更可能拥有它的特征类型（用于排序，不是证明）。
_GEOMETRY_FEATURE_AFFINITY: dict[str, tuple[str, ...]] = {
    "Cylinder": ("Cut", "Extrude", "Hole", "WizardHole", "Fillet"),
    "Planar": ("Extrude", "Cut", "Sketch", "Fillet", "Chamfer"),
    "Cone": ("Cut", "Extrude", "Hole", "Chamfer"),
    "Torus": ("Fillet", "Cut", "Extrude"),
    "Sphere": ("Dome", "Revolve"),
}

#: 这些特征名的实体可以要求参数修改，但不应被当成「原始设计特征」。
_NON_PARAMETRIC_HINTS = ("import", "imported", "body", "surface", "mesh", "sew")


def _feature_type_bonus(feature_type: str | None, geometry_type: str | None) -> float:
    """按几何类型给出特征类型亲和度加分。"""
    if not feature_type or not geometry_type:
        return 0.0
    affinity = _GEOMETRY_FEATURE_AFFINITY.get(str(geometry_type), ())
    folded = str(feature_type).casefold()
    for index, name in enumerate(affinity):
        if name.casefold() in folded:
            return 0.25 - 0.05 * index
    return 0.0


def _name_similarity(left: str | None, right: str | None) -> float:
    """轻量的名称相关度：完全相同加分，包含关系小加分。"""
    if not left or not right:
        return 0.0
    a, b = str(left).casefold(), str(right).casefold()
    if a == b:
        return 0.5
    if a in b or b in a:
        return 0.2
    return 0.0


def rank_candidates(
    entity: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """对候选特征排序并给出解析结论。

    返回 ``{"status": "resolved"|"ambiguous"|"unresolved", "best": {...},
    "ranked": [...], "reason": str|None}``；分数只用于排序，不当作能力证据。
    """
    geometry_type = str(entity.get("geometryType") or "Unknown")
    entity_type = str(entity.get("entityType") or "Unknown")
    dimension_hint = {
        str(name).casefold()
        for name in (entity.get("approximateGeometry") or {}).keys()
    }

    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        name = str(candidate.get("featureName") or candidate.get("name") or "")
        feature_type = str(candidate.get("featureType") or candidate.get("type") or "")
        base = float(candidate.get("confidence") or 0.0) * 0.5
        score = base
        score += _feature_type_bonus(feature_type, geometry_type)
        if any(hint in feature_type.casefold() for hint in dimension_hint if hint):
            score += 0.05
        if any(hint in name.casefold() for hint in _NON_PARAMETRIC_HINTS):
            score -= 0.3
        item = dict(candidate)
        item["score"] = round(max(0.0, min(1.0, score)), 3)
        scored.append(item)

    if not scored:
        return {
            "status": "unresolved",
            "best": None,
            "ranked": [],
            "reason": f"{entity_type}/{geometry_type} 没有可用的特征候选",
        }

    scored.sort(key=lambda item: item["score"], reverse=True)
    top = scored[0]
    if top["score"] <= 0.0:
        return {"status": "unresolved", "best": None, "ranked": scored, "reason": "候选特征置信度为 0"}
    if len(scored) > 1 and abs(top["score"] - scored[1]["score"]) < 0.05:
        return {
            "status": "ambiguous",
            "best": top,
            "ranked": scored,
            "reason": "多个候选特征得分接近，需要用户在高亮候选之间确认",
        }
    return {"status": "resolved", "best": top, "ranked": scored, "reason": None}


# --------------------------------------------------------------------------- COM 侧


def _probe(obj: Any, name: str, *args: Any) -> tuple[Any, str | None]:
    """调用 COM 成员并吞掉异常，返回 (值, 错误描述)。"""
    try:
        member = getattr(obj, name)
    except Exception as exc:  # noqa: BLE001
        return None, f"{name}: {type(exc).__name__}"
    try:
        return (member(*args) if callable(member) else member), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{name}: {type(exc).__name__}"


def _first(obj: Any, names: Iterable[str]) -> tuple[Any, str | None]:
    """按顺序尝试多个成员名。"""
    for name in names:
        value, _ = _probe(obj, name)
        if value is not None:
            return value, name
    return None, None


def _feature_descriptor(feature: Any) -> dict[str, Any]:
    """读取特征的名称与类型。"""
    name, _ = _first(feature, ("Name", "GetName"))
    type_name, _ = _first(feature, ("GetTypeName2", "GetTypeName"))
    return {
        "featureName": str(name) if name else "",
        "featureType": str(type_name) if type_name else "Unknown",
        "confidence": 0.9 if name else 0.0,
    }


def resolve_feature_from_com(entity_obj: Any, entity_type: str) -> dict[str, Any]:
    """从 COM 实体对象追溯所属特征。

    返回 ``{"status", "feature", "method", "evidence", "dimensionNames"}``。
    """
    evidence: list[str] = []
    feature = None
    method = "none"

    if entity_type == "Face":
        feature, error = _probe(entity_obj, "GetFeature")
        if error:
            evidence.append(error)
        method = "face_get_feature"
    elif entity_type == "Edge":
        faces, error = _probe(entity_obj, "GetTwoAdjacentFaces2")
        if error:
            evidence.append(error)
        method = "edge_adjacent_face"
        for face in list(faces or []):
            candidate, error = _probe(face, "GetFeature")
            if error:
                evidence.append(error)
                continue
            if candidate is not None:
                feature = candidate
                break
    elif entity_type == "Dimension":
        feature, error = _probe(entity_obj, "GetFeature")
        if error:
            evidence.append(error)
        method = "dimension_full_name"
    else:
        feature = entity_obj
        method = "selection_type"

    dimension_names: list[str] = []
    if entity_type == "Dimension":
        full_name, _ = _first(entity_obj, ("FullName",))
        if full_name:
            dimension_names.append(str(full_name))

    if feature is None:
        return {
            "status": "unresolved",
            "feature": None,
            "method": "none",
            "evidence": evidence[:8],
            "dimensionNames": dimension_names,
            "reason": "无法从该实体追溯到所属特征；可能是导入实体或只读几何",
        }

    descriptor = _feature_descriptor(feature)
    if not descriptor["featureName"]:
        return {
            "status": "unresolved",
            "feature": None,
            "method": method,
            "evidence": evidence[:8],
            "dimensionNames": dimension_names,
            "reason": "特征对象存在但读不到名称",
        }
    return {
        "status": "resolved",
        "feature": descriptor,
        "method": method,
        "evidence": evidence[:8],
        "dimensionNames": dimension_names,
    }


def list_feature_dimensions(feature: Any, *, limit: int = 64) -> list[str]:
    """枚举特征上的显示尺寸全名（如 ``D1@Sketch4``）。

    SolidWorks 的枚举入口在不同版本上有差异，因此这里按多个成员名探测；
    任一步失败即返回已收集到的结果，不抛异常。
    """
    names: list[str] = []
    display_dimension, _ = _first(feature, ("GetFirstDisplayDimension", "IGetFirstDisplayDimension"))
    seen = 0
    while display_dimension is not None and seen < limit:
        seen += 1
        dimension, _ = _first(display_dimension, ("GetDimension", "IGetDimension"))
        if dimension is not None:
            full_name, _ = _first(dimension, ("FullName",))
            if full_name:
                names.append(str(full_name))
        next_dimension, _ = _first(display_dimension, ("GetNext3", "GetNext2", "GetNext"))
        if next_dimension is None or next_dimension is display_dimension:
            break
        display_dimension = next_dimension
    return names


def resolve_entity_context(entity: Mapping[str, Any]) -> dict[str, Any]:
    """纯 DTO 侧的解析摘要：把实体已有候选重新排序，供 MCP 工具直接返回。"""
    candidates = list(entity.get("featureCandidates") or [])
    resolution = entity.get("resolution") or {}
    if not candidates:
        return {
            "status": resolution.get("status", "unresolved"),
            "best": None,
            "ranked": [],
            "reason": resolution.get("reason") or "实体没有特征候选",
            "method": resolution.get("method", "none"),
        }
    ranked = rank_candidates(entity, candidates)
    ranked["method"] = resolution.get("method", "none")
    if ranked["status"] == "unresolved" and resolution.get("status") == "resolved" and resolution.get("reason"):
        ranked["reason"] = resolution["reason"]
    return ranked
