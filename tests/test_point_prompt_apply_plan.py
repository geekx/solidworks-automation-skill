from __future__ import annotations

import sw_apply_plan as apply_module
import sw_edit_plan as edit_plan
import sw_selection_context as context_module

TOKEN = "doc-" + "a" * 24


class FakeAdapter:
    """不依赖 SolidWorks 的尺寸读写/重建适配器。"""

    def __init__(self, value_mm=6.0, *, rebuild_ok=True, corrupt_write=False):
        self.values = {"D1@Sketch4": value_mm}
        self.rebuild_ok = rebuild_ok
        self.corrupt_writes_remaining = 1 if corrupt_write else 0
        self.writes = []

    def document_token(self):
        return TOKEN

    def read_dimension_mm(self, dimension):
        return self.values.get(dimension)

    def write_dimension_mm(self, dimension, value_mm):
        self.writes.append((dimension, value_mm))
        offset = 0.5 if self.corrupt_writes_remaining > 0 else 0.0
        self.corrupt_writes_remaining = max(0, self.corrupt_writes_remaining - 1)
        self.values[dimension] = round(float(value_mm) + offset, 4)

    def rebuild(self):
        return {"ok": self.rebuild_ok, "method": "ForceRebuild3"}


def _context(revision=0):
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
        {"documentToken": TOKEN, "path": r"C:\work\Bracket.SLDPRT", "title": "Bracket", "documentType": "Part", "configuration": "Default"},
        {"revision": revision, "dirty": False, "units": "MMGS"},
        [snapshot],
    )


def _plan(context, *, risk="low", new_mm=8.0):
    entity = context["selection"]["objects"][0]
    return edit_plan.make_plan(
        {"kind": "modify_sketch_dimension", "dimension": "D1@Sketch4", "oldValueMm": 6.0, "newValueMm": new_mm},
        {
            "documentToken": context["document"]["documentToken"],
            "documentRevision": context["modelState"]["revision"],
            "selectionToken": context["selection"]["selectionToken"],
            "entityToken": entity["entityToken"],
        },
        risk=risk,
        invariants=["preserve_hole_center"],
    )


def test_apply_commits_and_bumps_revision(bridge):
    context = _context()
    adapter = FakeAdapter()
    result = apply_module.apply_plan(_plan(context), context, adapter, confirmed=True)
    assert result["status"] == "applied", result
    assert adapter.values["D1@Sketch4"] == 8.0
    assert result["revisionAfter"] == 1
    assert bridge.document_revision(TOKEN) == 1
    assert [entry["event"] for entry in bridge.read_journal()][-2:] == ["start", "commit"]


def test_readback_mismatch_rolls_back(bridge):
    context = _context()
    adapter = FakeAdapter(corrupt_write=True)
    result = apply_module.apply_plan(_plan(context), context, adapter, confirmed=True)
    assert result["status"] == "rolled_back"
    assert result["error_code"] == "E_READBACK_MISMATCH"
    assert adapter.values["D1@Sketch4"] == 6.0
    assert bridge.document_revision(TOKEN) == 0


def test_rebuild_failure_rolls_back(bridge):
    context = _context()
    adapter = FakeAdapter(rebuild_ok=False)
    result = apply_module.apply_plan(_plan(context), context, adapter, confirmed=True)
    assert result["status"] in {"rolled_back", "failed"}
    assert adapter.values["D1@Sketch4"] == 6.0
    assert any(entry["event"] in {"rollback", "rollback_failed"} for entry in bridge.read_journal())


def test_unconfirmed_medium_plan_does_not_write(bridge):
    context = _context()
    adapter = FakeAdapter()
    result = apply_module.apply_plan(_plan(context, risk="medium"), context, adapter, confirmed=False)
    assert result["status"] == "blocked"
    assert result["error_code"] == "E_CONFIRMATION_REQUIRED"
    assert adapter.writes == []


def test_token_mismatch_does_not_write(bridge):
    context = _context()
    plan = _plan(context)
    plan["target"]["documentRevision"] = 99
    adapter = FakeAdapter()
    result = apply_module.apply_plan(plan, context, adapter, confirmed=True)
    assert result["status"] == "blocked"
    assert adapter.writes == []


def test_undo_restores_previous_value(bridge):
    context = _context()
    adapter = FakeAdapter()
    applied = apply_module.apply_plan(_plan(context), context, adapter, confirmed=True)
    assert applied["status"] == "applied"

    undo = apply_module.undo_last_operation(adapter)
    assert undo["status"] == "undone"
    assert adapter.values["D1@Sketch4"] == 6.0
    assert undo["undoneEntryId"] == applied["entryId"]


def test_undo_refuses_when_revision_moved_on(bridge):
    context = _context()
    adapter = FakeAdapter()
    apply_module.apply_plan(_plan(context), context, adapter, confirmed=True)
    bridge.bump_document_revision(TOKEN)
    undo = apply_module.undo_last_operation(adapter)
    assert undo["status"] == "blocked"
    assert undo["error_code"] == "E_STALE_CONTEXT"


def test_undo_without_journal_is_blocked(bridge):
    undo = apply_module.undo_last_operation(FakeAdapter())
    assert undo["status"] == "blocked"


def test_context_refresh_after_apply_advances_revision():
    context = _context()
    apply_module.refresh_context_after_apply(context, 3)
    assert context["modelState"]["revision"] == 3
    assert context["staleness"]["status"] == "fresh"
