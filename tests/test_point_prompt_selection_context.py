from __future__ import annotations

import pytest

import sw_selection_context as context_module


def _snapshot(**overrides):
    defaults = dict(
        entity_type="Face",
        geometry_type="Cylinder",
        feature_name="Cut-Extrude3",
        feature_type="Cut",
        feature_confidence=0.93,
        geometry={"diameterMm": 6.0, "axis": [0, 0, 1], "centerMm": [20, 10, 5]},
        resolution_status="resolved",
        resolution_method="face_get_feature",
    )
    defaults.update(overrides)
    return context_module.EntitySnapshot(**defaults)


def _context(revision=42, mode="pinned", snapshots=None):
    return context_module.build_selection_context(
        {"path": r"C:\work\Bracket.SLDPRT", "title": "Bracket", "documentType": "Part", "configuration": "Default"},
        {"revision": revision, "dirty": False, "units": "MMGS"},
        [_snapshot()] if snapshots is None else snapshots,
        mode=mode,
    )


def test_document_token_is_stable_across_path_spelling():
    left = context_module.document_token(r"C:\Work\Bracket.SLDPRT", "Part")
    right = context_module.document_token("c:/work/bracket.sldprt", "Part")
    assert left == right
    assert left.startswith("doc-")
    assert left != context_module.document_token(r"C:\Work\Bracket.SLDPRT", "Assembly")


def test_entity_token_tracks_geometry_not_face_number():
    first = context_module.entity_token("Face", "Cut-Extrude3", {"diameterMm": 6.0})
    same = context_module.entity_token("Face", "Cut-Extrude3", {"diameterMm": 6.0})
    changed = context_module.entity_token("Face", "Cut-Extrude3", {"diameterMm": 8.0})
    assert first == same
    assert first != changed
    assert first.startswith("ent-")


def test_selection_token_changes_with_revision_and_membership():
    doc = "doc-" + "a" * 24
    entity = context_module.entity_token("Face", "Cut-Extrude3", {"diameterMm": 6.0})
    baseline = context_module.selection_token(doc, [entity], 42)
    assert baseline == context_module.selection_token(doc, [entity], 42)
    assert baseline != context_module.selection_token(doc, [entity], 43)
    assert baseline != context_module.selection_token(doc, [], 42)


def test_build_context_fills_tokens_and_schema_valid():
    context = _context()
    assert context["document"]["documentToken"].startswith("doc-")
    assert context["selection"]["selectionToken"].startswith("sel-")
    assert context["selection"]["activeEntityToken"] == context["selection"]["objects"][0]["entityToken"]
    assert context_module.validate_context(context) == []


def test_context_rejects_empty_selection_on_staleness_check():
    context = _context(snapshots=[])
    result = context_module.check_staleness(context)
    assert result["status"] == "unresolved"


def test_staleness_fresh_then_stale_on_revision_bump():
    context = _context()
    assert context_module.check_staleness(context, current_revision=42)["status"] == "fresh"
    stale = context_module.check_staleness(context, current_revision=43)
    assert stale["status"] == "stale"
    assert "revision" in stale["reason"]


def test_staleness_detects_other_document():
    context = _context()
    other = context_module.document_token(r"C:\work\Other.SLDPRT", "Part")
    result = context_module.check_staleness(context, current_document_token=other)
    assert result["status"] == "stale"


def test_bump_revision_rewrites_selection_token():
    context = _context()
    before = context["selection"]["selectionToken"]
    context_module.bump_revision(context, 43)
    assert context["modelState"]["revision"] == 43
    assert context["selection"]["selectionToken"] != before
    assert context["staleness"]["status"] == "fresh"


def test_active_entity_prefers_active_token():
    first = _snapshot(feature_name="A", geometry={"diameterMm": 6.0})
    second = _snapshot(feature_name="B", geometry={"diameterMm": 8.0})
    context = _context(snapshots=[first, second])
    context["selection"]["activeEntityToken"] = context["selection"]["objects"][1]["entityToken"]
    assert context_module.active_entity(context)["entityToken"] == context["selection"]["objects"][1]["entityToken"]


# --------------------------------------------------------------------------- COM 快照


class _Surface:
    def IsPlane(self):
        return False

    def IsCylinder(self):
        return True

    def CylinderParams(self):
        # 原点(米)、轴向、半径(米)
        return [0.02, 0.01, 0.005, 0.0, 0.0, 1.0, 0.003]


class _Feature:
    def __init__(self, name: str, type_name: str) -> None:
        self.Name = name
        self._type_name = type_name

    def GetTypeName2(self):
        return self._type_name


class _Face:
    def __init__(self) -> None:
        self._feature = _Feature("Cut-Extrude3", "Cut")

    def GetSurface(self):
        return _Surface()

    def GetFeature(self):
        return self._feature

    def GetName(self):
        raise AttributeError("faces have no Name")


class _Model:
    Extension = None


def test_snapshot_reads_cylinder_geometry_in_mm():
    snapshot = context_module.snapshot_from_com(_Face(), _Model(), type_id=2)
    assert snapshot.entity_type == "Face"
    assert snapshot.geometry_type == "Cylinder"
    assert snapshot.geometry["diameterMm"] == pytest.approx(6.0)
    assert snapshot.geometry["centerMm"] == pytest.approx([20.0, 10.0, 5.0])
    assert snapshot.feature_name == "Cut-Extrude3"
    assert snapshot.resolution_status == "resolved"


def test_snapshot_marks_unresolved_without_feature():
    class _BareFace:
        def GetSurface(self):
            return None

    snapshot = context_module.snapshot_from_com(_BareFace(), _Model(), type_id=2)
    assert snapshot.resolution_status in {"unresolved", "resolved"}
    assert snapshot.entity_type == "Face"
