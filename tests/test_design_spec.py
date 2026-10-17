"""设计规格校验回归测试。"""
import json

import pytest

from scripts.design_spec import (
    DesignSpecError,
    compare_verification,
    load_design_spec,
    to_mm,
    unit_scale,
    validate_design_spec,
)


def minimal_spec(**overrides):
    """构造一个最小可用的规格。"""
    spec = {
        "schema_version": "1.0",
        "part_name": "bracket",
        "units": "mm",
        "base": {"type": "rect_plate", "width": 100, "height": 60, "thickness": 6},
        "holes": [{"id": "H1", "type": "through", "diameter": 6.6, "position": [10, 10]}],
    }
    spec.update(overrides)
    return spec


def test_valid_spec_passes():
    """@brief 合法规格应通过并给出归一化结果。"""
    result = validate_design_spec(minimal_spec())

    assert result["status"] == "ok"
    assert result["errors"] == []
    normalized = result["normalized"]
    assert normalized["part_name"] == "bracket"
    assert normalized["base"]["width_mm"] == 100.0
    assert normalized["holes"][0]["diameter_mm"] == 6.6


def test_units_are_normalized_to_mm():
    """@brief 英寸制规格必须换算为毫米，避免 25.4 倍错误。"""
    spec = minimal_spec(
        units="in",
        base={"type": "rect_plate", "width": 2.0, "height": 1.0, "thickness": 0.25},
    )
    result = validate_design_spec(spec)

    assert result["status"] == "ok"
    base = result["normalized"]["base"]
    assert base["width_mm"] == pytest.approx(50.8)
    assert base["height_mm"] == pytest.approx(25.4)
    assert base["thickness_mm"] == pytest.approx(6.35)


def test_unsupported_unit_is_rejected():
    """@brief 未知单位必须报错，不能默默按毫米处理。"""
    result = validate_design_spec(minimal_spec(units="furlong"))

    assert result["status"] == "error"
    assert any(item["code"] == "SPEC_UNIT_UNSUPPORTED" for item in result["errors"])


def test_all_errors_reported_at_once():
    """@brief 应一次性返回全部问题，避免用户逐个试错。"""
    spec = minimal_spec(
        base={"type": "rect_plate", "width": -1, "height": 0, "thickness": 6},
        holes=[{"id": "H1", "type": "wrong_type", "diameter": 6.6, "position": [10, 10]}],
    )
    result = validate_design_spec(spec)

    assert result["status"] == "error"
    assert len(result["errors"]) >= 3


def test_duplicate_hole_id_is_rejected():
    """@brief 孔 id 重复会让检验记录无法追溯，必须报错。"""
    spec = minimal_spec(holes=[
        {"id": "H1", "type": "through", "diameter": 6.6, "position": [10, 10]},
        {"id": "H1", "type": "through", "diameter": 6.6, "position": [20, 10]},
    ])
    result = validate_design_spec(spec)

    assert any(item["code"] == "SPEC_HOLE_ID_DUPLICATE" for item in result["errors"])


def test_hole_without_id_gets_generated_id_and_warning():
    """@brief 缺 id 时自动补号，但要提示用户显式命名。"""
    spec = minimal_spec(holes=[{"type": "through", "diameter": 6.6, "position": [10, 10]}])
    result = validate_design_spec(spec)

    assert result["status"] == "ok"
    assert result["normalized"]["holes"][0]["id"] == "H1"
    assert any("未提供 id" in warning for warning in result["warnings"])


def test_blind_hole_requires_depth():
    """@brief 盲孔缺 depth 必须报错。"""
    spec = minimal_spec(holes=[{"id": "H1", "type": "blind", "diameter": 5.0, "position": [10, 10]}])
    result = validate_design_spec(spec)

    assert any(item["code"] == "SPEC_FIELD_MISSING" for item in result["errors"])


def test_counterbore_requires_extra_fields():
    """@brief 沉头孔缺沉孔直径/深度必须报错。"""
    spec = minimal_spec(holes=[{"id": "H1", "type": "counterbore", "diameter": 6.6, "position": [10, 10]}])
    result = validate_design_spec(spec)

    codes = {item["code"] for item in result["errors"]}
    assert "SPEC_FIELD_MISSING" in codes
    assert len(result["errors"]) >= 2


def test_oversized_fillet_radius_warns():
    """@brief 圆角半径超过板厚一半时应给出 DFM 警告。"""
    spec = minimal_spec(fillets=[{"radius": 10, "axis": "z"}])
    result = validate_design_spec(spec)

    assert result["status"] == "ok"
    assert any("板厚" in warning for warning in result["warnings"])


def test_conflicting_fillet_flags_rejected():
    """@brief convex_only 与 concave_only 同时为真必须报错。"""
    spec = minimal_spec(fillets=[{"radius": 2, "convex_only": True, "concave_only": True}])
    result = validate_design_spec(spec)

    assert any(item["code"] == "SPEC_FILLET_FLAGS" for item in result["errors"])


def test_schema_version_mismatch_warns_but_proceeds():
    """@brief 版本不一致只警告，不阻断。"""
    result = validate_design_spec(minimal_spec(schema_version="0.9"))

    assert result["status"] == "ok"
    assert any("schema_version" in warning for warning in result["warnings"])


def test_too_many_holes_rejected():
    """@brief 孔数超过上限应报错，防止意外的海量几何。"""
    spec = minimal_spec(holes=[
        {"id": f"H{index}", "type": "through", "diameter": 3.0, "position": [index, 0]}
        for index in range(250)
    ])
    result = validate_design_spec(spec)

    assert any(item["code"] == "SPEC_HOLES_TOO_MANY" for item in result["errors"])


def test_non_dict_spec_rejected():
    """@brief 非对象输入应明确报错。"""
    result = validate_design_spec(["not", "a", "spec"])

    assert result["status"] == "error"
    assert result["errors"][0]["code"] == "SPEC_NOT_OBJECT"


def test_empty_spec_rejected():
    """@brief 没有任何几何的规格应报错，避免建出空文档。"""
    result = validate_design_spec({"schema_version": "1.0", "units": "mm"})

    assert any(item["code"] == "SPEC_EMPTY" for item in result["errors"])


def test_base_type_none_is_allowed():
    """@brief 明确声明 base.type=none 表示只建孔等后续特征。"""
    result = validate_design_spec(minimal_spec(base={"type": "none"}))

    assert result["status"] == "ok"


def test_load_spec_missing_file():
    """@brief 文件不存在时给出明确错误而不是抛异常。"""
    result = load_design_spec("definitely/not/here.json")

    assert result["status"] == "error"
    assert result["errors"][0]["code"] == "SPEC_FILE_MISSING"


def test_load_spec_invalid_json(tmp_path):
    """@brief JSON 语法错误应被捕获并报告。"""
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    result = load_design_spec(path)

    assert result["errors"][0]["code"] == "SPEC_JSON_INVALID"


def test_load_spec_round_trip(tmp_path):
    """@brief 从磁盘读取合法规格。"""
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(minimal_spec()), encoding="utf-8")

    result = load_design_spec(path)

    assert result["status"] == "ok"
    assert result["source_path"].endswith("spec.json")


def test_unit_scale_and_to_mm():
    """@brief 单位换算函数本身。"""
    assert unit_scale("mm") == 1.0
    assert unit_scale("IN") == 25.4
    assert to_mm(2, "in") == pytest.approx(50.8)
    with pytest.raises(DesignSpecError):
        unit_scale("stone")


# ---------------------------------------------------------------- 验证比对

def test_compare_verification_passes_on_match():
    """@brief 实测与期望一致时通过。"""
    result = compare_verification(
        {"envelope_mm": [100.0, 60.0, 6.0], "hole_count": 4, "mass_g": 500.0},
        {"envelope_mm": [100.0, 60.0, 6.0], "hole_count": 4, "tolerance_mm": 0.1},
    )

    assert result["status"] == "pass"
    assert result["failed"] == []


def test_compare_verification_flags_dimension_mismatch():
    """@brief 尺寸超差必须报告为警告并要求人工复核。"""
    result = compare_verification(
        {"envelope_mm": [100.0, 60.0, 6.0], "hole_count": 4},
        {"envelope_mm": [120.0, 60.0, 6.0], "hole_count": 4, "tolerance_mm": 0.1},
    )

    assert result["status"] == "warn"
    assert "envelope_mm" in result["failed"]
    assert result["manual_review_required"] is True


def test_compare_verification_tolerance_is_honoured():
    """@brief 偏差在公差内应通过。"""
    result = compare_verification(
        {"envelope_mm": [100.05, 60.0, 6.0], "hole_count": 4},
        {"envelope_mm": [100.0, 60.0, 6.0], "hole_count": 4, "tolerance_mm": 0.1},
    )

    assert result["status"] == "pass"


def test_compare_verification_skipped_without_expectations():
    """@brief 未提供 verify 段时明确标记跳过，而不是假装通过。"""
    result = compare_verification({"envelope_mm": [1, 2, 3]}, {})

    assert result["status"] == "skipped"


def test_compare_verification_reports_unreadable_geometry():
    """@brief 读不到几何时该项判为失败，不能默认通过。"""
    result = compare_verification(
        {"envelope_mm": None, "hole_count": 2},
        {"envelope_mm": [100.0, 60.0, 6.0], "hole_count": 2},
    )

    assert result["status"] == "warn"
    assert "envelope_mm" in result["failed"]


def test_compare_verification_mass_bounds():
    """@brief 质量上下限分别校验。"""
    result = compare_verification(
        {"mass_g": 400.0},
        {"min_mass_g": 300.0, "max_mass_g": 500.0},
    )
    assert result["status"] == "pass"

    too_heavy = compare_verification({"mass_g": 900.0}, {"max_mass_g": 500.0})
    assert too_heavy["status"] == "warn"
