"""Point & Prompt 的 SelectionContext 构建、token 派生与失效判定。

设计约束（见 `docs/point-and-prompt/interaction-spec.md`）：

- 只产出 JSON DTO，不把 COM RCW 传出本模块；
- token 由「文档 + 实体语义 + 几何签名」派生，禁止把 ``Face1``/``Edge1`` 当永久身份；
- 上下文必须带 document/revision，供执行前校验，防止跨文档、跨重建误操作；
- COM 读取全部做保护：任何属性缺失或异常都降级为 ``unresolved`` 并记录证据。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # 既支持 ``import sw_selection_context``，也支持 ``import scripts.sw_selection_context``
    from . import sw_cad_bridge as bridge
    from .sw_entity_reference import geometry_signature
except ImportError:  # pragma: no cover - 顶层模块导入路径
    import sw_cad_bridge as bridge
    from sw_entity_reference import geometry_signature

SCHEMA_VERSION = "1.0"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "point-and-prompt" / "selection-context.schema.json"

#: 单位系统枚举（swUserPreferenceIntegerValue_e / swUnitSystem_e 的常用映射）。
_UNIT_NAMES = {0: "MKS", 1: "CGS", 2: "MMGS", 3: "IPS"}

#: 兜底用的 swSelectType_e 编号；真实机器上优先按 COM 接口指纹识别实体类型。
_SELECT_TYPE_FALLBACK = {
    1: "Edge",
    2: "Face",
    3: "Vertex",
    4: "Sketch",
    9: "Dimension",
    14: "Component",
    16: "Feature",
}

_DOCUMENT_TYPES = {1: "Part", 2: "Assembly", 3: "Drawing"}


# --------------------------------------------------------------------------- 基础工具


def utc_now() -> str:
    """RFC 3339 UTC 时间戳。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_path(path: str | os.PathLike[str]) -> str:
    """规范化 Windows 路径：绝对化、统一分隔符、大小写归一（用于 token 派生）。"""
    text = os.path.expandvars(str(path)).strip().strip('"')
    if not text:
        return ""
    try:
        text = os.path.abspath(text)
    except OSError:
        pass
    return os.path.normpath(text).replace("/", "\\").casefold()


def document_token(path: str | os.PathLike[str], document_type: str = "Part") -> str:
    """由文档路径与类型派生稳定 token。未保存文档使用标题 + 类型。"""
    seed = f"{normalize_path(path)}|{document_type}" if str(path).strip() else f"untitled|{document_type}"
    return "doc-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def entity_token(entity_type: str, feature_name: str | None, geometry: Any = None) -> str:
    """由实体类型、所属特征与几何签名派生语义 token。"""
    seed = "|".join(
        [
            str(entity_type or "Unknown").casefold(),
            str(feature_name or "").casefold(),
            geometry_signature(geometry) if geometry else "no-geometry",
        ]
    )
    return "ent-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def selection_token(document_token_value: str, entity_tokens: Iterable[str], revision: int) -> str:
    """由文档、实体集合与 revision 派生选择 token；任一变化都会换 token。"""
    seed = "|".join([document_token_value, str(int(revision)), ",".join(sorted(entity_tokens))])
    return "sel-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def schema_path() -> Path:
    """返回 SelectionContext Schema 路径。"""
    return SCHEMA_PATH


def load_schema() -> dict[str, Any]:
    """加载 SelectionContext JSON Schema。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- DTO


@dataclass(frozen=True)
class EntitySnapshot:
    """一个被选对象的最小语义快照（纯数据，可跨线程/进程传递）。"""

    entity_type: str
    geometry_type: str = "Unknown"
    display_name: str | None = None
    geometry: Mapping[str, Any] = field(default_factory=dict)
    feature_name: str | None = None
    feature_type: str | None = None
    feature_confidence: float = 0.0
    dimension_names: tuple[str, ...] = ()
    resolution_status: str = "unresolved"
    resolution_method: str = "none"
    resolution_reason: str | None = None
    evidence: tuple[str, ...] = ()
    persistent_reference: str | None = None
    raw_token: str | None = None

    def to_entity(self) -> dict[str, Any]:
        """转为 Schema 中的 entity 对象。"""
        token = self.raw_token or entity_token(self.entity_type, self.feature_name, dict(self.geometry))
        entity: dict[str, Any] = {
            "entityToken": token,
            "entityType": self.entity_type,
            "geometryType": self.geometry_type,
            "resolution": {
                "status": self.resolution_status,
                "method": self.resolution_method,
                "confidence": round(float(self.feature_confidence), 3),
            },
        }
        if self.display_name:
            entity["displayName"] = self.display_name
        if self.geometry:
            entity["approximateGeometry"] = {key: value for key, value in dict(self.geometry).items() if value is not None}
        if self.feature_name:
            candidate = {
                "featureName": self.feature_name,
                "featureType": self.feature_type or "Unknown",
                "confidence": round(float(self.feature_confidence or 0.5), 3),
            }
            if self.dimension_names:
                candidate["dimensionNames"] = list(self.dimension_names)
            if self.evidence:
                candidate["evidence"] = list(self.evidence[:8])
            entity["featureCandidates"] = [candidate]
        if self.resolution_reason:
            entity["resolution"]["reason"] = self.resolution_reason
        if self.evidence:
            entity["resolution"]["evidence"] = list(self.evidence[:8])
        if self.persistent_reference:
            entity["persistentReference"] = self.persistent_reference
        return entity


def build_selection_context(
    document: Mapping[str, Any],
    model_state: Mapping[str, Any],
    snapshots: Sequence[EntitySnapshot],
    *,
    mode: str = "pinned",
    source: str = "python_com",
    host_version: str = "",
    addin_version: str = "",
    active_index: int = 0,
    staleness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """组装 SelectionContext，并补齐 documentToken / selectionToken。"""
    doc = dict(document)
    doc.setdefault("documentType", "Part")
    if not doc.get("documentToken"):
        doc["documentToken"] = document_token(doc.get("path", ""), doc.get("documentType", "Part"))
    doc.setdefault("title", Path(str(doc.get("path", ""))).stem or "Untitled")
    doc.setdefault("configuration", "Default")

    state = dict(model_state)
    state.setdefault("revision", 0)
    state.setdefault("dirty", False)
    state.setdefault("units", "UNKNOWN")

    entities = [snapshot.to_entity() for snapshot in snapshots]
    tokens = [entity["entityToken"] for entity in entities]
    selection = {
        "selectionToken": selection_token(doc["documentToken"], tokens, int(state["revision"])),
        "mode": mode if mode in {"hover", "selected", "set", "pinned"} else "selected",
        "objects": entities,
    }
    if entities and 0 <= active_index < len(entities):
        selection["activeEntityToken"] = entities[active_index]["entityToken"]
    if mode == "pinned":
        selection["pinnedAt"] = utc_now()

    context: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "document": doc,
        "modelState": state,
        "selection": selection,
        "provenance": {
            "source": source,
            "capturedAt": utc_now(),
            "hostVersion": host_version,
            "addinVersion": addin_version,
            "bridgeRevision": int(bridge.read_state().get("bridgeRevision", 0)),
        },
        "staleness": dict(staleness) if staleness else {"status": "fresh", "checkedAt": utc_now()},
    }
    return context


def find_entity(context: Mapping[str, Any], entity_token_value: str) -> dict[str, Any] | None:
    """在上下文里按 token 查找实体。"""
    for entity in (context.get("selection") or {}).get("objects") or []:
        if entity.get("entityToken") == entity_token_value:
            return entity
    return None


def active_entity(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """返回 Prompt 锚定的实体：优先 activeEntityToken，否则第一个对象。"""
    selection = context.get("selection") or {}
    token = selection.get("activeEntityToken")
    if token:
        entity = find_entity(context, token)
        if entity:
            return entity
    objects = selection.get("objects") or []
    return objects[0] if objects else None


# --------------------------------------------------------------------------- 校验与失效


def validate_context(context: Mapping[str, Any]) -> list[str]:
    """按 Schema 校验上下文；返回错误列表（空表示通过）。"""
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(load_schema())
        return [f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}" for error in validator.iter_errors(dict(context))]
    except ImportError:
        return _structural_errors(context)


def _structural_errors(context: Mapping[str, Any]) -> list[str]:
    """无 jsonschema 时的最小结构校验。"""
    errors: list[str] = []
    for key in ("schemaVersion", "document", "modelState", "selection", "provenance"):
        if key not in context:
            errors.append(f"missing field: {key}")
    document = context.get("document") or {}
    for key in ("documentToken", "title", "documentType"):
        if not document.get(key):
            errors.append(f"document.{key}: required")
    selection = context.get("selection") or {}
    if not selection.get("selectionToken"):
        errors.append("selection.selectionToken: required")
    if not isinstance(selection.get("objects"), list):
        errors.append("selection.objects: must be a list")
    return errors


def check_staleness(
    context: Mapping[str, Any],
    *,
    current_revision: int | None = None,
    current_document_token: str | None = None,
    max_age_seconds: float | None = 3600,
    now: datetime | None = None,
) -> dict[str, Any]:
    """判定上下文是否仍然有效。

    返回 ``{"status": "fresh"|"stale"|"unresolved", "reason": ...}``；stale 表示
    revision 或文档已变化，unresolved 表示上下文本身不完整。
    """
    reasons: list[str] = []
    state = context.get("modelState") or {}
    document = context.get("document") or {}
    selection = context.get("selection") or {}

    if not selection.get("objects"):
        return {"status": "unresolved", "reason": "selection.objects 为空：没有可操作目标"}

    if current_document_token and document.get("documentToken") != current_document_token:
        reasons.append("documentToken 与当前文档不一致")

    if current_revision is not None and int(state.get("revision", -1)) != int(current_revision):
        reasons.append(f"revision 已从 {state.get('revision')} 变为 {current_revision}")

    if max_age_seconds is not None:
        captured = context.get("provenance", {}).get("capturedAt")
        if captured:
            try:
                captured_at = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
                reference = now or datetime.now(timezone.utc)
                age = (reference - captured_at).total_seconds()
                if age > max_age_seconds:
                    reasons.append(f"上下文已过期（{int(age)}s > {int(max_age_seconds)}s）")
            except ValueError:
                pass

    if reasons:
        return {"status": "stale", "reason": "；".join(reasons), "checkedAt": utc_now()}
    return {"status": "fresh", "checkedAt": utc_now()}


def stamp_staleness(context: dict[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """把失效判定写回上下文并返回。"""
    context["staleness"] = dict(result)
    return context


def bump_revision(context: dict[str, Any], revision: int) -> dict[str, Any]:
    """把上下文推进到新 revision，并重算 selectionToken。"""
    state = context.setdefault("modelState", {})
    state["revision"] = int(revision)
    selection = context.setdefault("selection", {})
    tokens = [entity.get("entityToken", "") for entity in selection.get("objects") or []]
    selection["selectionToken"] = selection_token(
        (context.get("document") or {}).get("documentToken", ""), tokens, int(revision)
    )
    context["staleness"] = {"status": "fresh", "checkedAt": utc_now()}
    return context


# --------------------------------------------------------------------------- COM 采集


def _probe(obj: Any, name: str, *args: Any) -> tuple[Any, str | None]:
    """调用 COM 成员；失败返回 (None, 原因)。"""
    try:
        member = getattr(obj, name)
    except Exception as exc:  # noqa: BLE001 - COM 失败样式多样
        return None, f"{name}: {type(exc).__name__}"
    try:
        return (member(*args) if callable(member) else member), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{name}({len(args)} args): {type(exc).__name__}"


def _first_success(obj: Any, names: Sequence[str]) -> tuple[Any, str | None, list[str]]:
    """按顺序尝试多个成员名，返回首个成功值。"""
    evidence: list[str] = []
    for name in names:
        value, error = _probe(obj, name)
        if error is None and value is not None:
            return value, name, evidence
        if error:
            evidence.append(error)
    return None, None, evidence


def _to_mm(value: Any) -> float | None:
    """米 → 毫米，保留 4 位小数；非数值返回 None。"""
    try:
        result = float(value) * 1000.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return round(result, 4)


def _vector(values: Any, *, scale: float = 1000.0) -> list[float] | None:
    """把坐标数组转为毫米三元组。"""
    try:
        items = list(values)
    except TypeError:
        return None
    if len(items) < 3:
        return None
    out: list[float] = []
    for item in items[:3]:
        try:
            number = float(item) * scale
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        out.append(round(number, 4))
    return out


def _detect_entity_type(obj: Any, type_id: int | None) -> str:
    """按 COM 接口指纹识别实体类型，识别不了再退回 swSelectType 编号。"""
    probes = (
        ("Face", ("GetSurface", "GetLoops", "GetTrimCurves2")),
        ("Edge", ("GetCurve", "GetTwoAdjacentFaces2")),
        ("Vertex", ("GetPoint",)),
        ("Dimension", ("GetDimension", "FullName")),
        ("Sketch", ("GetSketchSegments", "GetSketchPoints2")),
        ("Component", ("GetPathName", "GetXform")),
        ("Feature", ("GetTypeName2", "GetTypeName")),
    )
    for entity_type, members in probes:
        for member in members:
            try:
                getattr(obj, member)
            except Exception:  # noqa: BLE001
                continue
            return entity_type
    if type_id is not None:
        return _SELECT_TYPE_FALLBACK.get(int(type_id), "Unknown")
    return "Unknown"


def _face_geometry(face: Any, evidence: list[str]) -> tuple[str, dict[str, Any]]:
    """读取圆柱/平面等面几何的近似参数。"""
    geometry: dict[str, Any] = {}
    surface, _, probe_evidence = _first_success(face, ("GetSurface",))
    evidence.extend(probe_evidence)
    if surface is None:
        return "Unknown", geometry

    checks = (
        ("IsPlane", "Planar"),
        ("IsCylinder", "Cylinder"),
        ("IsCone", "Cone"),
        ("IsSphere", "Sphere"),
        ("IsTorus", "Torus"),
    )
    geometry_type = "Unknown"
    for member, label in checks:
        value, _ = _probe(surface, member)
        if value:
            geometry_type = label
            break

    if geometry_type == "Cylinder":
        params, _, probe_evidence = _first_success(surface, ("CylinderParams",))
        evidence.extend(probe_evidence)
        if params:
            values = list(params)
            if len(values) >= 7:
                geometry["centerMm"] = _vector(values[0:3]) or None
                geometry["axis"] = _vector(values[3:6], scale=1.0) or None
                radius = _to_mm(values[6])
                if radius:
                    geometry["diameterMm"] = round(radius * 2, 4)
                    geometry["radiusMm"] = radius
    elif geometry_type == "Planar":
        params, _, probe_evidence = _first_success(surface, ("PlaneParams",))
        evidence.extend(probe_evidence)
        if params and len(list(params)) >= 6:
            values = list(params)
            geometry["centerMm"] = _vector(values[0:3]) or None
            geometry["normal"] = _vector(values[3:6], scale=1.0) or None
    return geometry_type, {key: value for key, value in geometry.items() if value is not None}


def _edge_geometry(edge: Any, evidence: list[str]) -> tuple[str, dict[str, Any]]:
    """读取直线/圆弧边的近似参数。"""
    geometry: dict[str, Any] = {}
    curve, _, probe_evidence = _first_success(edge, ("GetCurve",))
    evidence.extend(probe_evidence)
    if curve is None:
        return "Unknown", geometry
    for member, label in (("IsLine", "Line"), ("IsCircle", "Circle"), ("IsArc", "Arc"), ("IsSpline", "Spline")):
        value, _ = _probe(curve, member)
        if value:
            geometry_type = label
            break
    else:
        geometry_type = "Unknown"
    if geometry_type == "Circle":
        params, _, probe_evidence = _first_success(curve, ("CircleParams",))
        evidence.extend(probe_evidence)
        if params:
            values = list(params)
            if len(values) >= 7:
                geometry["radiusMm"] = _to_mm(values[6])
                geometry["centerMm"] = _vector(values[0:3]) or None
    elif geometry_type == "Line":
        params, _, probe_evidence = _first_success(curve, ("LineParams",))
        evidence.extend(probe_evidence)
        if params:
            values = list(params)
            if len(values) >= 6:
                start = _vector(values[0:3])
                end = _vector(values[3:6])
                if start and end:
                    geometry["boxMinMm"] = [min(a, b) for a, b in zip(start, end)]
                    geometry["boxMaxMm"] = [max(a, b) for a, b in zip(start, end)]
    return geometry_type, {key: value for key, value in geometry.items() if value is not None}


def _feature_of(obj: Any, entity_type: str, evidence: list[str]) -> tuple[str | None, str | None, list[str]]:
    """尽力追溯所属特征与尺寸名。"""
    dimension_names: list[str] = []
    feature_obj = None
    if entity_type == "Face":
        feature_obj, _, probe_evidence = _first_success(obj, ("GetFeature", "IGetFeature"))
        evidence.extend(probe_evidence)
    elif entity_type == "Edge":
        faces, _, probe_evidence = _first_success(obj, ("GetTwoAdjacentFaces2",))
        evidence.extend(probe_evidence)
        if faces:
            for face in list(faces):
                candidate, _, probe_evidence = _first_success(face, ("GetFeature", "IGetFeature"))
                evidence.extend(probe_evidence)
                if candidate is not None:
                    feature_obj = candidate
                    break
    elif entity_type == "Dimension":
        full_name, _, probe_evidence = _first_success(obj, ("FullName", "GetNameForSelection"))
        evidence.extend(probe_evidence)
        if full_name:
            dimension_names.append(str(full_name))
            feature_obj, _, probe_evidence = _first_success(obj, ("GetFeature",))
            evidence.extend(probe_evidence)
    elif entity_type in {"Feature", "Sketch", "Component"}:
        feature_obj = obj

    if feature_obj is None:
        return None, None, dimension_names

    name, _, probe_evidence = _first_success(feature_obj, ("Name", "GetName"))
    evidence.extend(probe_evidence)
    type_name, _, probe_evidence = _first_success(feature_obj, ("GetTypeName2", "GetTypeName"))
    evidence.extend(probe_evidence)
    if entity_type == "Dimension" and not dimension_names:
        full_name, _, probe_evidence = _first_success(obj, ("FullName",))
        evidence.extend(probe_evidence)
        if full_name:
            dimension_names.append(str(full_name))
    return (str(name) if name else None), (str(type_name) if type_name else None), dimension_names


def _persistent_reference(obj: Any, model: Any, evidence: list[str]) -> str | None:
    """尝试生成 SolidWorks 持久引用（base64）；不可用则返回 None。"""
    extension = None
    try:
        extension = model.Extension
    except Exception as exc:  # noqa: BLE001
        evidence.append(f"model.Extension: {type(exc).__name__}")
        return None
    try:
        reference = extension.GetPersistReference3(obj)
    except Exception as exc:  # noqa: BLE001
        evidence.append(f"GetPersistReference3: {type(exc).__name__}")
        return None
    if not reference:
        return None
    try:
        import base64

        return base64.b64encode(bytes(reference)).decode("ascii")[:2048]
    except Exception:  # noqa: BLE001
        return None


def snapshot_from_com(obj: Any, model: Any, *, type_id: int | None = None) -> EntitySnapshot:
    """把一个 COM 选择对象转为 EntitySnapshot。"""
    evidence: list[str] = []
    entity_type = _detect_entity_type(obj, type_id)
    geometry_type = "Unknown"
    geometry: dict[str, Any] = {}

    if entity_type == "Face":
        geometry_type, geometry = _face_geometry(obj, evidence)
    elif entity_type == "Edge":
        geometry_type, geometry = _edge_geometry(obj, evidence)
    elif entity_type == "Vertex":
        point, _, probe_evidence = _first_success(obj, ("GetPoint",))
        evidence.extend(probe_evidence)
        if point:
            geometry["centerMm"] = _vector(point)
        geometry_type = "Point"

    feature_name, feature_type, dimension_names = _feature_of(obj, entity_type, evidence)
    status, method, reason = "unresolved", "none", None
    if feature_name:
        status, method = "resolved", "face_get_feature" if entity_type == "Face" else "feature_tree_scan"
    elif entity_type in {"Feature", "Sketch", "Component", "Dimension"}:
        status, method = "resolved", "selection_type"
    elif evidence:
        reason = "无法从对象追溯到所属特征；可能是导入实体或只读几何"

    display_name, _, probe_evidence = _first_success(obj, ("Name", "GetName"))
    evidence.extend(probe_evidence)

    return EntitySnapshot(
        entity_type=entity_type,
        geometry_type=geometry_type,
        display_name=str(display_name) if display_name else None,
        geometry={key: value for key, value in geometry.items() if value is not None},
        feature_name=feature_name,
        feature_type=feature_type,
        feature_confidence=0.9 if feature_name else 0.0,
        dimension_names=tuple(dimension_names),
        resolution_status=status,
        resolution_method=method,
        resolution_reason=reason,
        evidence=tuple(evidence[:12]),
        persistent_reference=_persistent_reference(obj, model, evidence),
    )


def collect_selection(model: Any, *, mode: str = "pinned") -> dict[str, Any]:
    """从当前 SolidWorks 文档的 SelectionManager 采集 SelectionContext。"""
    document = describe_document(model)
    snapshots: list[EntitySnapshot] = []
    evidence: list[str] = []
    try:
        selection_manager = model.SelectionManager
        count = int(selection_manager.GetSelectedObjectCount2(-1) or 0)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法读取 SolidWorks 选择集：{type(exc).__name__}: {exc}") from exc

    for index in range(1, count + 1):
        try:
            obj = selection_manager.GetSelectedObject6(index, -1)
        except Exception as exc:  # noqa: BLE001
            evidence.append(f"GetSelectedObject6[{index}]: {type(exc).__name__}")
            continue
        if obj is None:
            continue
        type_id = None
        try:
            type_id = int(selection_manager.GetSelectedObjectType3(index, -1))
        except Exception as exc:  # noqa: BLE001
            evidence.append(f"GetSelectedObjectType3[{index}]: {type(exc).__name__}")
        snapshots.append(snapshot_from_com(obj, model, type_id=type_id))

    revision = bridge.document_revision(document["documentToken"])
    model_state = describe_model_state(model, revision=revision)
    context = build_selection_context(
        document,
        model_state,
        snapshots,
        mode=mode,
        source="python_com",
    )
    if evidence:
        context.setdefault("extensions", {})["collectionEvidence"] = evidence[:16]
    return context


def describe_document(model: Any) -> dict[str, Any]:
    """读取活动文档的标识信息。"""
    path, _, _ = _first_success(model, ("GetPathName",))
    title, _, _ = _first_success(model, ("GetTitle",))
    doc_type_id, _, _ = _first_success(model, ("GetType",))
    try:
        doc_type = _DOCUMENT_TYPES.get(int(doc_type_id), "Part")
    except (TypeError, ValueError):
        doc_type = "Part"
    configuration, _, _ = _first_success(model, ("ConfigurationManager",))
    config_name = None
    if configuration is not None:
        active, _, _ = _first_success(configuration, ("ActiveConfiguration",))
        if active is not None:
            name, _, _ = _first_success(active, ("Name",))
            config_name = str(name) if name else None

    return {
        "documentToken": document_token(str(path or ""), doc_type),
        "path": str(path or ""),
        "title": str(title or (Path(str(path)).stem if path else "Untitled")),
        "documentType": doc_type,
        "configuration": config_name or "Default",
        "documentRevision": bridge.document_revision(document_token(str(path or ""), doc_type)),
    }


def describe_model_state(model: Any, *, revision: int = 0) -> dict[str, Any]:
    """读取模型状态：修订号、脏标记、单位与文档级错误计数。"""
    dirty_flag, _, _ = _first_success(model, ("GetSaveFlag",))
    units_id, _, _ = _first_success(model, ("GetUnits",))
    try:
        units = _UNIT_NAMES.get(int(units_id), "UNKNOWN")
    except (TypeError, ValueError):
        units = "UNKNOWN"

    state: dict[str, Any] = {
        "revision": int(revision),
        "dirty": bool(dirty_flag),
        "units": units,
    }
    feature_count, _, _ = _first_success(model, ("GetFeatureCount",))
    if feature_count is not None:
        try:
            state["featureCount"] = int(feature_count)
        except (TypeError, ValueError):
            pass
    wrong_count, _, _ = _first_success(model, ("GetWhatsWrongCount",))
    if wrong_count is not None:
        try:
            state["invalidFeatureCount"] = int(wrong_count)
        except (TypeError, ValueError):
            pass
    return state
