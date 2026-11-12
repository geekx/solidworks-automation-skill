from __future__ import annotations

import json
from pathlib import Path

import sw_edit_plan as edit_plan
import sw_selection_context as context_module

ROOT = Path(__file__).resolve().parents[1]


def _context(revision=42):
    snapshot = context_module.EntitySnapshot(
        entity_type="Face",
        geometry_type="Cylinder",
        feature_name="Cut-Extrude3",
        feature_type="Cut",
        feature_confidence=0.93,
        geometry={"diameterMm": 6.0},
        resolution_status="resolved",
        resolution_method="face_get_feature",
    )
    return context_module.build_selection_context(
        {"path": r"C:\work\Bracket.SLDPRT", "title": "Bracket", "documentType": "Part", "configuration": "Default"},
        {"revision": revision, "dirty": False, "units": "MMGS"},
        [snapshot],
    )


def _plan(context, *, risk="low", new_mm=8.0, old_mm=6.0, kind="modify_sketch_dimension"):
    entity = context["selection"]["objects"][0]
    return edit_plan.make_plan(
        {"kind": kind, "dimension": "D1@Sketch4", "oldValueMm": old_mm, "newValueMm": new_mm},
        {
            "documentToken": context["document"]["documentToken"],
            "documentRevision": context["modelState"]["revision"],
            "selectionToken": context["selection"]["selectionToken"],
            "entityToken": entity["entityToken"],
        },
        risk=risk,
        invariants=["preserve_hole_center"],
        summary="把孔改成 Ø8",
    )


def test_schema_is_valid_json():
    schema = json.loads((ROOT / "docs" / "point-and-prompt" / "edit-plan.schema.json").read_text(encoding="utf-8"))
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert "modify_sketch_dimension" in json.dumps(schema)


def test_valid_plan_passes_validation():
    context = _context()
    assert edit_plan.validate_plan(_plan(context)) == []


def test_unknown_operation_is_rejected():
    context = _context()
    plan = _plan(context)
    plan["operation"] = {"kind": "run_arbitrary_python", "code": "print(1)"}
    errors = edit_plan.validate_plan(plan)
    assert any("白名单" in error for error in errors)


def test_negative_value_is_rejected():
    context = _context()
    plan = _plan(context)
    plan["operation"]["newValueMm"] = -1
    assert edit_plan.validate_plan(plan)


def test_preview_ready_for_low_risk_confirmed_context():
    context = _context()
    preview = edit_plan.preview_plan(_plan(context), context, current_value_mm=6.0)
    assert preview["status"] == "ready"
    assert preview["error_code"] is None
    assert preview["diff"]["deltaMm"] == 2.0


def test_medium_risk_requires_confirmation():
    context = _context()
    plan = _plan(context, risk="medium")
    assert plan["requiresConfirmation"] is True
    preview = edit_plan.preview_plan(plan, context, current_value_mm=6.0)
    assert preview["status"] == "needs_confirmation"
    assert preview["error_code"] == "E_CONFIRMATION_REQUIRED"
    confirmed = edit_plan.preview_plan(plan, context, current_value_mm=6.0, confirmed=True)
    assert confirmed["status"] == "ready"


def test_token_mismatch_blocks():
    context = _context()
    plan = _plan(context)
    plan["target"]["documentRevision"] = 41
    preview = edit_plan.preview_plan(plan, context, current_value_mm=6.0)
    assert preview["status"] == "blocked"
    assert preview["error_code"] == "E_TOKEN_MISMATCH"


def test_old_value_mismatch_blocks():
    context = _context()
    preview = edit_plan.preview_plan(_plan(context), context, current_value_mm=7.0)
    assert preview["status"] == "blocked"
    assert preview["error_code"] == "E_PLAN_INVALID"


def test_capability_level_blocks_undeclared_operation():
    context = _context()
    capabilities = {"capabilities": [{"id": "solidworks_point_prompt_parametric_edit_apply", "level": "not_implemented"}]}
    preview = edit_plan.preview_plan(_plan(context), context, current_value_mm=6.0, capabilities=capabilities)
    assert preview["status"] == "blocked"
    assert preview["error_code"] == "E_CAPABILITY_BLOCKED"


def test_repository_declares_apply_as_pilot():
    assert edit_plan.capability_level("solidworks_point_prompt_parametric_edit_apply") == "pilot"


def test_extreme_ratio_is_warned_but_not_blocked():
    context = _context()
    preview = edit_plan.preview_plan(_plan(context, new_mm=60.0), context, current_value_mm=6.0)
    assert preview["status"] == "ready"
    assert any(item["result"] == "WARN" for item in preview["checks"] if item["id"] == "value_range")


def test_preview_without_reader_marks_old_value_pending():
    context = _context()
    preview = edit_plan.preview_plan(_plan(context), context)
    assert any(item["id"] == "old_value" and item["result"] == "WARN" for item in preview["checks"])
