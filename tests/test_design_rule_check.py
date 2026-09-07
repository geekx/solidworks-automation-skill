"""设计规则检查（DRC）引擎与声明式 Profile 的无 COM 回归测试。"""
from __future__ import annotations

import json

import pytest

from scripts import design_rule_check as drc
from scripts import design_rule_profiles as profiles


def _doc(**overrides):
    document = {
        "documentId": "demo",
        "units": "mm",
        "features": [
            {"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0, "edgeDistance": 1.0, "thread": "M6", "threadNominal": 6, "threadEngagement": 4}},
            {"id": "H2", "type": "hole", "parameters": {"diameter": 6, "x": 5, "y": 0}},
        ],
        "metadata": {
            "design": {"minWallThicknessMm": 0.8},
            "assembly": {"componentCount": 3, "mateCount": 0, "interferences": [{"a": "p1", "b": "p2"}]},
            "drawing": {"requiredDimensionsMissing": ["overall_length"], "dimensionsWithoutTolerance": 4},
        },
        "standardParts": [
            {"id": "G1", "type": "spur_gear", "teeth": 12, "is_pointed": False, "profile_shift_coeff": 0},
            {"id": "S1", "type": "compression_spring", "spring_index": 2.5, "solid_length_mm": 30, "free_length_mm": 25},
            {"id": "K1", "type": "roller_sprocket", "teeth": 11},
        ],
    }
    document.update(overrides)
    return document


def _by_id(report):
    result = {}
    for check in report["checks"]:
        result.setdefault(check["id"], check)
    return result


# ---- Profile 校验 ----


def test_profile_rejects_unknown_fields_and_operators():
    with pytest.raises(profiles.DrcProfileError):
        profiles.validate_profile({"thresholds": {"unknownKey": 1}})
    with pytest.raises(profiles.DrcProfileError):
        profiles.validate_profile({"customRules": [{"id": "X", "field": "d", "operator": "regex", "value": 1, "message": "m"}]})
    with pytest.raises(profiles.DrcProfileError):
        profiles.validate_profile({"customRules": [{"id": "bad id!", "field": "d", "operator": "lt", "value": 1, "message": "m"}]})


def test_profile_validates_and_normalizes_custom_rule():
    validated = profiles.validate_profile(
        {"customRules": [{"id": "C1", "appliesTo": "hole", "field": "diameter", "operator": "lt", "value": 8, "message": "small"}]}
    )
    rule = validated["customRules"][0]
    assert rule["category"] == "custom"
    assert rule["severity"] == "warning"
    assert rule["appliesTo"] == "hole"


def test_profile_merge_tightens_minimum_threshold():
    merged = profiles.merge_profiles(
        [{"thresholds": {"minWallThicknessMm": 1.0}}, {"thresholds": {"minWallThicknessMm": 2.0}}]
    )
    assert merged["thresholds"]["minWallThicknessMm"] == 2.0


# ---- 引擎 ----


def test_build_report_flags_all_categories():
    report = drc.build_drc_report(_doc())
    assert report["status"] == "fail"
    checks = _by_id(report)
    assert checks["DRC-GEO-001"]["status"] == "fail"
    assert checks["DRC-HOLE-001"]["status"] == "fail"
    assert checks["DRC-STD-004"]["status"] == "fail"
    assert checks["DRC-ASM-001"]["status"] == "fail"
    assert checks["DRC-DRW-001"]["status"] == "fail"
    assert report["reviewRequired"] is True


def test_thresholds_from_profile_change_outcome():
    doc = _doc(metadata={"design": {"minWallThicknessMm": 1.2}})
    # 默认阈值 1.0：1.2 > 1.0 但 < 1.5 -> warning
    default = _by_id(drc.build_drc_report(doc))
    assert default["DRC-GEO-001"]["status"] == "warning"
    # 提高阈值到 2.0 -> fail
    strict = _by_id(drc.build_drc_report(doc, profiles=[{"thresholds": {"minWallThicknessMm": 2.0}}]))
    assert strict["DRC-GEO-001"]["status"] == "fail"


def test_disabled_rule_is_skipped():
    report = drc.build_drc_report(_doc(), profiles=[{"disabledRules": ["DRC-ASM-001"]}])
    assert "DRC-ASM-001" not in _by_id(report)


def test_custom_rule_fires_per_target():
    profile = {"customRules": [{"id": "CUST-SMALL", "appliesTo": "hole", "field": "diameter", "operator": "lt", "value": 8, "message": "孔径过小"}]}
    report = drc.build_drc_report(_doc(), profiles=[profile])
    custom = [c for c in report["checks"] if c["id"] == "CUST-SMALL" and c["status"] == "warning"]
    assert len(custom) == 2  # H1 与 H2 都命中


def test_custom_rule_document_scalar():
    profile = {"customRules": [{"id": "CUST-COMP", "appliesTo": "document", "field": "componentCount", "operator": "gte", "value": 3, "severity": "major", "message": "组件多"}]}
    report = drc.build_drc_report(_doc(), profiles=[profile])
    hit = _by_id(report)["CUST-COMP"]
    assert hit["status"] == "fail"


def test_clean_document_passes():
    clean = {
        "documentId": "clean",
        "units": "mm",
        "features": [{"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0, "edgeDistance": 5.0}}],
        "metadata": {"design": {"minWallThicknessMm": 3.0}},
        "standardParts": [{"id": "G", "type": "spur_gear", "teeth": 24, "is_pointed": False}],
    }
    report = drc.build_drc_report(clean)
    assert report["status"] == "pass"
    assert report["summary"]["fail"] == 0


def test_missing_document_id_blocks():
    report = drc.build_drc_report({"units": "mm"})
    assert report["status"] == "blocked"


def test_unit_conversion_inch_to_mm():
    doc = {
        "documentId": "in",
        "units": "in",
        "features": [{"id": "H", "type": "hole", "parameters": {"diameter": 0.25, "x": 0, "y": 0, "edgeDistance": 0.02}}],
    }
    report = drc.build_drc_report(doc)
    hole_edge = _by_id(report).get("DRC-HOLE-001")
    # 0.02in=0.508mm < 要求(半径 0.25in=3.175mm)，应 fail
    assert hole_edge is not None and hole_edge["status"] == "fail"


def test_list_rules_shape():
    catalog = drc.list_rules()
    assert catalog["rules"]
    assert "minWallThicknessMm" in catalog["defaultThresholds"]
    assert "lt" in catalog["customRule"]["operators"]


def test_write_report_versions_and_hashes(tmp_path):
    doc_path = tmp_path / "d.json"
    doc_path.write_text(json.dumps(_doc()), encoding="utf-8")
    out = tmp_path / "drc.json"
    first = drc.write_drc_report(doc_path, out)
    assert first["artifacts"][0]["sha256"]
    assert first["reportPath"].endswith("drc.json")
    second = drc.write_drc_report(doc_path, out)
    assert second["reportPath"].endswith("drc.v1.json")  # 不覆盖旧文件
