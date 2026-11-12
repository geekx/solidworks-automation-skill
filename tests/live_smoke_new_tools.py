"""
真机冒烟测试：验证新增的建模/测量/规格工具在真实 SolidWorks 上可用。

这是**真机回归**，需要本机安装并授权 SolidWorks。它不在默认 pytest 集合里
（文件名不以 test_ 开头），必须显式运行：

    python tests/live_smoke_new_tools.py

所有产物写入 tests/output/live_smoke/，不触碰用户已打开的文档：脚本开始时记录
文档列表，结束时只关闭自己创建的那些。

退出码 0 表示全部通过；非 0 表示有失败项，详细原因打印在 stdout。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "tests" / "output" / "live_smoke"

RESULTS = []


def record(name, ok, detail=None):
    """记录一项结果。"""
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}")
    if detail and not ok:
        text = json.dumps(detail, ensure_ascii=False, default=str) if not isinstance(detail, str) else detail
        print(f"        {text[:900]}")


def section(title):
    """打印分节标题。"""
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main():
    """执行真机冒烟测试。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from scripts import sw_connect, sw_measure, sw_edge_select
    from scripts.sw_part import (
        sketch, sketch_circle, sketch_corner_rectangle, extrude_boss, fillet, chamfer,
    )
    from scripts.design_spec import validate_design_spec, compare_verification
    from scripts.build_from_spec import build_from_spec, expand_hole_patterns

    # ---------------------------------------------------------------- 连接
    section("1. 连接与版本")
    preexisting = []
    try:
        sw, _ = sw_connect.connect_solidworks(wait_seconds=10)
        version = sw_connect.get_sw_version(sw)
        record("连接 SolidWorks", True, version)
        preexisting = [sw_connect.get_com_member(doc, "GetTitle") for doc in (sw_connect.get_com_member(sw, "GetDocuments") or [])]
        record("记录已打开文档（保护用户文档）", True, {"preexisting": preexisting})
    except Exception as exc:
        record("连接 SolidWorks", False, f"{type(exc).__name__}: {exc}")
        return 1

    # -------------------------------------------------- new_document 修复验证
    section("2. P0-1 new_document 不再误认已打开的文档")
    created_models = []
    try:
        model = sw_connect.new_document(sw, "part")
        created_models.append(model)
        title = sw_connect.get_com_member(model, "GetTitle")
        record(
            "new_document 返回的确实是新文档",
            title not in preexisting,
            {"new_title": title, "preexisting": preexisting},
        )
    except Exception as exc:
        record("new_document 创建零件", False, f"{type(exc).__name__}: {exc}")
        return 1

    # ------------------------------------------------------------ 建模工具
    section("3. 建模：草图+拉伸、圆角、倒角")
    try:
        with sketch(model, "Front Plane") as sketch_ref:
            sketch_corner_rectangle(model, -0.06, -0.04, 0.06, 0.04)
        base_feature = extrude_boss(model, sketch_ref, 0.008)
        record("创建 120x80x8 板", base_feature is not None,
               {"feature": sw_connect.get_com_member(base_feature, "Name") if base_feature else None})
    except Exception as exc:
        record("创建 120x80x8 板", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:600]}")

    # 语义选边
    try:
        descriptors, errors = sw_edge_select.iter_model_edges(model)
        record("遍历模型边线", len(descriptors) > 0, {"edge_count": len(descriptors), "errors": errors[:3]})

        vertical, rejected = sw_edge_select.filter_edges(descriptors, axis="z")
        record("按 Z 轴筛选竖直边", len(vertical) == 4,
               {"matched": len(vertical), "sample": [item.get("length_mm") for item in vertical[:6]]})

        selection = sw_edge_select.select_edges(model, {"axis": "z", "convex_only": True})
        record("选择凸出的竖直边", selection["status"] == "ok",
               {"selected_count": selection.get("selected_count"), "errors": selection.get("errors", [])[:3]})
    except Exception as exc:
        record("语义选边", False, f"{type(exc).__name__}: {exc}")

    # 圆角
    try:
        selection = sw_edge_select.select_edges(model, {"axis": "z"})
        fillet_feature = fillet(model, 0.004) if selection["status"] == "ok" else None
        model.ClearSelection2(True)
        record("创建 R4 竖直边圆角", fillet_feature is not None,
               {"selected": selection.get("selected_count"),
                "feature": sw_connect.get_com_member(fillet_feature, "Name") if fillet_feature else None,
                "errors": selection.get("errors", [])[:3]})
    except Exception as exc:
        record("创建 R4 竖直边圆角", False, f"{type(exc).__name__}: {exc}")

    # 倒角必须在**独立文档**上验证：圆角已经把 4 条竖直边变成了切边，
    # 在同一模型上继续倒角选到的是圆角产生的切边，不是原始竖边。
    try:
        chamfer_model = sw_connect.new_document(sw, "part")
        created_models.append(chamfer_model)
        with sketch(chamfer_model, "Front Plane") as chamfer_sketch:
            sketch_corner_rectangle(chamfer_model, 0.0, 0.0, 0.120, 0.080)
        extrude_boss(chamfer_model, chamfer_sketch, 0.008)
        selection = sw_edge_select.select_edges(chamfer_model, {"axis": "z"})
        chamfer_feature = chamfer(chamfer_model, 0.002, 45.0) if selection["status"] == "ok" else None
        chamfer_model.ClearSelection2(True)
        record("创建 C2 竖直边倒角（独立文档）", chamfer_feature is not None,
               {"selected": selection.get("selected_count"),
                "feature": sw_connect.get_com_member(chamfer_feature, "Name") if chamfer_feature else None})
    except Exception as exc:
        record("创建 C2 竖直边倒角（独立文档）", False, f"{type(exc).__name__}: {exc}")

    # 测量
    section("4. 测量：包围盒与质量属性")
    try:
        box = sw_measure.collect_bounding_box(model)
        size = box.get("size_mm")
        ok = bool(size) and abs(size[0] - 120.0) < 0.5 and abs(size[1] - 80.0) < 0.5 and abs(size[2] - 8.0) < 0.5
        record("包围盒 = 120 x 80 x 8 mm", ok, {"size_mm": size, "errors": box.get("errors")})
    except Exception as exc:
        record("包围盒", False, f"{type(exc).__name__}: {exc}")

    try:
        mass = sw_measure.collect_mass_properties(model)
        volume = mass.get("volume_mm3")
        expected_volume = 120.0 * 80.0 * 8.0
        # 圆角会去掉一点体积，允许 2% 偏差
        ok = bool(volume) and abs(volume - expected_volume) / expected_volume < 0.02
        record("体积接近 76800 mm3", ok,
               {"volume_mm3": volume, "expected": expected_volume, "material_assigned": mass.get("material_assigned"),
                "warnings": mass.get("warnings"), "errors": mass.get("errors")})
    except Exception as exc:
        record("质量属性", False, f"{type(exc).__name__}: {exc}")

    # 密度设置：真机实测 IMassProperty.Density 只读、SetOverrideMass 不存在，
    # 唯一可行路径是 swMaterialPropertyDensity(7) 偏好项。
    try:
        steel = sw_measure.collect_mass_properties(model, density_kg_m3=7850.0)
        volume_mm3 = steel.get("volume_mm3") or 0.0
        expected_mass_g = volume_mm3 * 7850.0 / 1_000_000.0
        actual_mass_g = steel.get("mass_g") or 0.0
        ok = (
            steel.get("density_overridden") is True
            and not steel.get("errors")
            and abs(actual_mass_g - expected_mass_g) / max(expected_mass_g, 1e-9) < 0.02
        )
        record("设置密度 7850 kg/m3 后质量正确", ok,
               {"mass_g": actual_mass_g, "expected_mass_g": round(expected_mass_g, 3),
                "implied_density": steel.get("implied_density_kg_m3"),
                "previous_density": steel.get("previous_density_kg_m3"),
                "errors": steel.get("errors")})
    except Exception as exc:
        record("设置密度后质量正确", False, f"{type(exc).__name__}: {exc}")

    # --------------------------------------------------------- 设计规格构建
    section("5. 规格驱动建模（design_spec_build 内核）")
    spec = {
        "schema_version": "1.0",
        "part_name": "live_smoke_bracket",
        "units": "mm",
        "base": {"type": "rect_plate", "width": 100, "height": 60, "thickness": 6},
        # 孔位按图纸标注：原点在板料左下角，位置为孔中心到左/下边的距离。
        "holes": [
            {"id": "H1", "type": "through", "diameter": 6.6, "position": [10, 10]},
            {"id": "H2", "type": "through", "diameter": 6.6, "position": [90, 10]},
            {"id": "H3", "type": "through", "diameter": 6.6, "position": [10, 50]},
            {"id": "H4", "type": "through", "diameter": 6.6, "position": [90, 50]},
        ],
        "verify": {"envelope_mm": [100, 60, 6], "hole_count": 4, "tolerance_mm": 0.5},
    }
    validation = validate_design_spec(spec)
    record("规格校验通过", validation["status"] == "ok", validation.get("errors"))

    if validation["status"] == "ok":
        try:
            spec_model = sw_connect.new_document(sw, "part")
            created_models.append(spec_model)
            audit = build_from_spec(spec_model, validation["normalized"], apply_fillets=True, verify_geometry=True)
            record("按规格构建零件", audit["status"] in ("ok", "warn"),
                   {"status": audit["status"], "errors": audit.get("errors", [])[:5],
                    "warnings": audit.get("warnings", [])[:5]})
            record("规格回读：4 个孔全部创建", audit.get("hole_count_created") == 4,
                   {"created": audit.get("hole_count_created"), "requested": audit.get("hole_count_requested")})

            verification = audit.get("verification", {})
            record("规格验证：包围盒与孔数符合", verification.get("status") == "pass",
                   {"status": verification.get("status"), "failed": verification.get("failed"),
                    "checks": verification.get("checks")})

            saved = sw_connect.save_document(spec_model, str(OUTPUT_DIR / "live_smoke_bracket.sldprt"))
            record("保存规格零件", bool(saved), str(OUTPUT_DIR / "live_smoke_bracket.sldprt"))
        except Exception as exc:
            record("按规格构建零件", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:800]}")

    # 孔阵列展开（纯计算）
    try:
        holes = expand_hole_patterns([
            {"id": "B", "type": "through", "diameter_mm": 5.0, "position_mm": [0.0, 0.0, 0.0],
             "pattern": {"type": "circular", "count": 6, "radius": 30.0, "start_angle": 0.0}},
        ])
        record("圆周阵列展开为 6 个孔", len(holes) == 6,
               {"count": len(holes), "positions": [hole["position_mm"][:2] for hole in holes[:3]]})
    except Exception as exc:
        record("孔阵列展开", False, f"{type(exc).__name__}: {exc}")

    # --------------------------------------------------------------- 清理
    section("6. 清理：只关闭本脚本创建的文档")
    closed = []
    for doc in created_models:
        try:
            title = sw_connect.get_com_member(doc, "GetTitle")
            sw.CloseDoc(title)
            closed.append(title)
        except Exception as exc:
            closed.append(f"<failed: {exc}>")
    try:
        remaining = [sw_connect.get_com_member(doc, "GetTitle") for doc in (sw_connect.get_com_member(sw, "GetDocuments") or [])]
    except Exception:
        remaining = None
    record("关闭脚本创建的文档", True, {"closed": closed, "remaining": remaining})
    record("用户原有文档未受影响",
           remaining is None or all(title in remaining for title in preexisting),
           {"preexisting": preexisting, "remaining": remaining})

    # --------------------------------------------------------------- 汇总
    section("汇总")
    failed = [item for item in RESULTS if not item["ok"]]
    print(f"总计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    report_path = OUTPUT_DIR / "live_smoke_report.json"
    report_path.write_text(
        json.dumps({"results": RESULTS, "failed_count": len(failed)}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"报告: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
