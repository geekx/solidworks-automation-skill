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


def test_ligament_rule_flags_thin_web():
    doc = {
        "documentId": "web",
        "units": "mm",
        "features": [
            {"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0}},
            {"id": "H2", "type": "hole", "parameters": {"diameter": 6, "x": 7, "y": 0}},
        ],
    }
    checks = _by_id(drc.build_drc_report(doc))
    # 中心距 7 - 3 - 3 = 1mm < 默认 2mm -> fail
    assert checks["DRC-HOLE-005"]["status"] == "fail"


def test_thread_vs_thickness_rule():
    doc = {
        "documentId": "thin",
        "units": "mm",
        "features": [{"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0, "thread": "M6", "threadNominal": 6}}],
        "metadata": {"plate": {"thicknessMm": 4.0}},
    }
    checks = _by_id(drc.build_drc_report(doc))
    # 4mm 板厚 < 1.0*6 默认? threadEngagementRatio 默认 1.0 -> required 6 > 4 -> warning
    assert checks["DRC-HOLE-006"]["status"] == "warning"


def test_relation_rules_datum_bom_coverage():
    doc = {
        "documentId": "rel",
        "units": "mm",
        "features": [{"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0}}],
        "metadata": {
            "gdt": [{"symbol": "position", "datums": ["A", "B", "C"]}],
            "datums": [{"id": "A"}, {"id": "B"}],
            "bom": [{"id": "b1", "featureType": "hole", "quantity": 3}],
            "drawing": {"holeCallouts": ["⌀8"]},
        },
    }
    checks = _by_id(drc.build_drc_report(doc))
    assert checks["DRC-REL-001"]["status"] == "fail"  # 基准 C 未声明
    assert checks["DRC-REL-002"]["status"] == "warning"  # ⌀6 未被 ⌀8 覆盖
    assert checks["DRC-REL-003"]["status"] == "warning"  # BOM 3 vs 特征 1


def test_checks_carry_standard_clause():
    checks = _by_id(drc.build_drc_report(_doc()))
    assert checks["DRC-GEO-001"].get("standard")
    assert checks["DRC-HOLE-005"].get("clause")


def test_report_has_fingerprint_and_risk():
    report = drc.build_drc_report(_doc())
    assert report["riskScore"]["band"] in {"none", "low", "medium", "high"}
    assert all("fingerprint" in c for c in report["checks"])
    assert "audit" in report


def test_rule_packs_listed_and_applied():
    from scripts import design_rule_profiles as prof

    names = [p["name"] for p in prof.list_rule_packs()]
    assert "drilling_panel" in names and "machined_bracket" in names
    # drilling_panel 把孔边距 ratio 提到 2.0：edge 5 对 ⌀6 -> required 6 -> fail
    doc = {
        "documentId": "pack",
        "units": "mm",
        "features": [{"id": "H1", "type": "hole", "parameters": {"diameter": 6, "x": 0, "y": 0, "edgeDistance": 5.0}}],
    }
    default = _by_id(drc.build_drc_report(doc))
    packed = _by_id(drc.build_drc_report(doc, rule_packs=["drilling_panel"]))
    assert "DRC-HOLE-001" not in default  # 默认 ratio 1.0 -> required 3 -> pass(不产生fail项)
    assert packed["DRC-HOLE-001"]["status"] == "fail"


def test_baseline_diff_and_waiver_gate():
    doc = _doc()
    base = drc.build_drc_report(doc)
    waivers = [{"ruleId": "DRC-STD-004", "target": "S1", "reason": "客户签字", "owner": "赵工", "expiresOn": "2026-12-31"}]
    report = drc.build_drc_report(doc, baseline=base, waivers=waivers, as_of_date="2026-09-08")
    assert report["summary"]["waived"] >= 1
    assert report["audit"]["diff"]["counts"]["persisting"] >= 1


def test_bad_rule_pack_name_blocks():
    report = drc.build_drc_report(_doc(), rule_packs=["../evil"])
    assert report["status"] == "blocked"


def test_fastener_countersink_rules_integrated():
    doc = {
        "documentId": "csk",
        "units": "mm",
        "features": [
            {"id": "C1", "type": "hole", "parameters": {"diameter": 6.6, "thread": "M6", "counterbore": {"diameter": 9.0, "depth": 5.0, "side": "back"}, "matingSide": "back"}},
        ],
        "metadata": {"fastenerStacks": [{"id": "S1", "holeId": "C1", "protrudes": True, "matingHasRelief": False, "gapMm": 0.0}]},
    }
    checks = _by_id(drc.build_drc_report(doc))
    assert checks["DRC-CSK-002"]["status"] == "fail"   # 头径不容纳
    assert checks["DRC-CSK-004"]["status"] == "fail"   # 沉头朝配合面
    assert checks["DRC-FIT-001"]["status"] == "fail"   # 跨零件无避让
    assert checks["DRC-CSK-004"].get("clause")         # 带条款出处


def test_no_countersink_skips_fastener_rules():
    report = drc.build_drc_report(_doc())
    checks = _by_id(report)
    # _doc() 的孔无沉头 -> DRC-CSK-000 info 跳过
    assert checks.get("DRC-CSK-000", {}).get("status") == "info"


def test_write_report_versions_and_hashes(tmp_path):
    doc_path = tmp_path / "d.json"
    doc_path.write_text(json.dumps(_doc()), encoding="utf-8")
    out = tmp_path / "drc.json"
    first = drc.write_drc_report(doc_path, out)
    assert first["artifacts"][0]["sha256"]
    assert first["reportPath"].endswith("drc.json")
    second = drc.write_drc_report(doc_path, out)
    assert second["reportPath"].endswith("drc.v1.json")  # 不覆盖旧文件
