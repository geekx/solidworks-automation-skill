from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "point-and-prompt"


def _load(name: str) -> dict:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def _context() -> dict:
    return {
        "schemaVersion": "1.0",
        "document": {
            "documentToken": "doc-" + "a" * 24,
            "path": "C:/work/Bracket.SLDPRT",
            "title": "Bracket",
            "documentType": "Part",
            "configuration": "Default",
        },
        "modelState": {"revision": 42, "dirty": True, "units": "MMGS"},
        "selection": {
            "selectionToken": "sel-" + "b" * 24,
            "mode": "pinned",
            "objects": [
                {
                    "entityToken": "ent-" + "c" * 24,
                    "entityType": "Face",
                    "geometryType": "Cylinder",
                    "approximateGeometry": {"diameterMm": 6.0},
                    "featureCandidates": [
                        {"featureName": "Cut-Extrude3", "featureType": "Cut", "confidence": 0.93}
                    ],
                    "resolution": {"status": "resolved", "method": "face_get_feature", "confidence": 0.93},
                }
            ],
        },
        "provenance": {"source": "addin", "capturedAt": "2026-11-12T12:00:00Z"},
    }


def _plan() -> dict:
    return {
        "schemaVersion": "1.0",
        "planId": "plan-001",
        "createdAt": "2026-11-12T12:00:00Z",
        "target": {
            "documentToken": "doc-" + "a" * 24,
            "documentRevision": 42,
            "selectionToken": "sel-" + "b" * 24,
            "entityToken": "ent-" + "c" * 24,
        },
        "operation": {
            "kind": "modify_sketch_dimension",
            "feature": "Cut-Extrude3",
            "dimension": "D1@Sketch4",
            "oldValueMm": 6.0,
            "newValueMm": 8.0,
        },
        "invariants": ["preserve_hole_center"],
        "risk": "low",
        "requiresConfirmation": True,
    }


def test_schemas_are_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(_load("selection-context.schema.json"))
    jsonschema.Draft202012Validator.check_schema(_load("edit-plan.schema.json"))


def test_selection_context_accepts_representative_document():
    jsonschema.Draft202012Validator(_load("selection-context.schema.json")).validate(_context())


def test_selection_context_rejects_unknown_entity_type():
    context = _context()
    context["selection"]["objects"][0]["entityType"] = "BRepFace"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_load("selection-context.schema.json")).validate(context)


def test_selection_context_rejects_face_number_tokens():
    context = _context()
    context["selection"]["objects"][0]["entityToken"] = "Face7"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_load("selection-context.schema.json")).validate(context)


def test_edit_plan_accepts_whitelisted_operation():
    jsonschema.Draft202012Validator(_load("edit-plan.schema.json")).validate(_plan())


def test_edit_plan_rejects_arbitrary_code_operation():
    plan = _plan()
    plan["operation"] = {"kind": "run_python", "code": "import os; os.remove('x')"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_load("edit-plan.schema.json")).validate(plan)


def test_edit_plan_requires_confirmation_for_high_risk():
    plan = _plan()
    plan["risk"] = "high"
    plan["requiresConfirmation"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_load("edit-plan.schema.json")).validate(plan)


def test_edit_plan_rejects_dimension_without_feature_suffix():
    plan = _plan()
    plan["operation"]["dimension"] = "D1"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_load("edit-plan.schema.json")).validate(plan)


def test_every_plan_kind_has_a_declared_capability():
    import sw_edit_plan as edit_plan

    declared = {capability["id"] for capability in edit_plan.load_capabilities()["capabilities"]}
    for kind, capability_id in edit_plan.CAPABILITY_BY_KIND.items():
        assert kind in edit_plan.WHITELIST, kind
        assert capability_id in declared, capability_id


def test_interaction_spec_and_schemas_are_linked_docs():
    roadmap = (DOCS / "roadmap.md").read_text(encoding="utf-8")
    assert "7.1 同窗口并行建模、画图与评估" in roadmap
    assert "7.2 非标自动化中等复杂案例" in roadmap
    assert "Cursor Swarm：Coordinator + 2–5 个子 Agent 并行开发" not in roadmap
