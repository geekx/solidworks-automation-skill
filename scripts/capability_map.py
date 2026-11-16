"""能力地图：把 `capabilities.yaml`、MCP 工具和脚本按工程域聚合。

目的：让 Agent 在执行前先看到「原生能力有多少、各自什么等级、对应哪个工具」，
而不是靠临时 probe 脚本反复试探。等级永远来自 `capabilities.yaml`，本模块只做
分组与展示，不新增能力声明。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "capabilities.yaml"

#: 工程域 → 能力 id、MCP 工具、脚本。工具名不存在时会被过滤掉。
DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "id": "part_modeling",
        "label": "零件建模：草图、拉伸/切除、旋转、圆角、倒角、抽壳、阵列、镜像、孔",
        "capabilities": ["part_and_features", "holes_and_finishing", "parameter_updates"],
        "tools": [
            "solidworks_create_basic_part",
            "solidworks_sketch_and_extrude",
            "solidworks_revolve",
            "solidworks_fillet",
            "solidworks_chamfer",
            "solidworks_shell",
            "solidworks_pattern",
            "solidworks_mirror_feature",
            "solidworks_create_hole_feature",
            "solidworks_inspect_hole_features",
            "solidworks_update_dimension",
            "solidworks_document_units",
        ],
        "scripts": ["scripts/sw_part.py", "scripts/sw_hole_features.py"],
        "summary": "常规参数化零件；草图支持圆/矩形/角点矩形/槽/多边形，随后拉伸凸台或切除。",
    },
    {
        "id": "sheet_metal",
        "label": "钣金：基体法兰、折弯、展开图",
        "capabilities": ["sheet_metal"],
        "tools": ["solidworks_create_sheet_metal_flange"],
        "scripts": ["scripts/sw_sheet_metal.py", "scripts/sw_export.py"],
        "summary": "闭合轮廓生成平板基体，开放折线生成带折弯的法兰；K 因子与折弯半径显式输入。",
    },
    {
        "id": "weldments",
        "label": "焊件：结构构件、切割清单",
        "capabilities": ["weldments"],
        "tools": [],
        "scripts": ["scripts/sw_weldment.py"],
        "summary": "需要本机 .sldlfp 型材文件；MCP 未封装，按脚本调用并复核切割清单。",
    },
    {
        "id": "standard_parts",
        "label": "标准件：渐开线直齿轮、压缩弹簧、链轮",
        "capabilities": [
            "standard_parts_generation",
            "external_spur_gear_geometry",
            "open_helical_spring",
            "approximate_roller_sprocket",
        ],
        "tools": ["solidworks_create_gear"],
        "scripts": ["scripts/sw_gear.py", "scripts/standard_parts_geometry.py", "scripts/sw_spring.py", "scripts/sw_sprocket.py"],
        "summary": "齿轮只覆盖标准外啮合直齿轮；斜齿/锥齿/蜗轮蜗杆/变位需专用路线或人工复核。",
    },
    {
        "id": "assembly",
        "label": "装配：组件、Mate、干涉、替换",
        "capabilities": ["assembly_and_mates"],
        "tools": [
            "solidworks_add_component",
            "solidworks_set_component_fixed",
            "solidworks_add_coincident_mate",
            "solidworks_add_distance_mate",
            "solidworks_add_concentric_mate",
            "solidworks_interference_check",
            "solidworks_replace_component_preserving_transform",
            "solidworks_bounding_box",
        ],
        "scripts": ["scripts/sw_assembly.py"],
        "summary": "组件放置、三类常用 Mate、干涉检查与保留变换的组件替换。",
    },
    {
        "id": "motion",
        "label": "运动：Motion Study、旋转马达、结果门禁",
        "capabilities": ["motion_study"],
        "tools": ["solidworks_add_rotary_motor", "solidworks_inspect_motion_studies", "solidworks_validate_motion_study"],
        "scripts": ["scripts/sw_motion.py"],
        "summary": "创建算例与马达、计算播放，并校验结果是否过期。",
    },
    {
        "id": "drawing",
        "label": "工程图：GB/T 图纸、尺寸链、孔表、BOM",
        "capabilities": ["drawing_dimension_insertion", "drawings_and_bom"],
        "tools": ["solidworks_generate_drawing", "solidworks_inspect_drawing", "solidworks_review_drawing"],
        "scripts": ["scripts/sw_drawing.py", "scripts/sw_drawing_package.py"],
        "summary": "按 DrawingSpec 出图并回读结构；图纸交付仍需人工目视复核。",
    },
    {
        "id": "export",
        "label": "导出与交付：STEP/STL/PDF/DXF、BOM、Pack and Go",
        "capabilities": ["export_delivery", "pack_and_go"],
        "tools": [
            "solidworks_export_active",
            "solidworks_batch_export_files",
            "solidworks_export_assembly_bom",
            "solidworks_pack_and_go",
            "solidworks_import_step_part",
        ],
        "scripts": ["scripts/sw_export.py", "scripts/sw_delivery.py"],
        "summary": "导出后回读产物和哈希；Pack and Go 超出基础装配时按 pilot 处理。",
    },
    {
        "id": "review",
        "label": "审查：预览图、DFM、DRC 设计审计",
        "capabilities": ["geometry_and_delivery_review", "design_rule_check", "dfm_checks"],
        "tools": [
            "solidworks_review_active",
            "cadstudio_check_drc",
            "cadstudio_list_drc_rules",
            "cadstudio_list_drc_rule_packs",
            "cadstudio_check_dfm",
        ],
        "scripts": ["scripts/sw_review.py", "scripts/design_rule_check.py", "scripts/dfm_review.py"],
        "summary": "审查结果给证据，不替代工程签核；缺关键输入返回 blocked。",
    },
    {
        "id": "configuration",
        "label": "配置与属性：配置族、自定义属性、公差、改名",
        "capabilities": [
            "configurations_and_design_tables",
            "custom_properties_and_bom",
            "tolerance_extraction",
            "property_based_file_rename",
        ],
        "tools": [
            "solidworks_inspect_configurations",
            "solidworks_create_configuration",
            "solidworks_activate_configuration",
            "solidworks_set_custom_properties",
        ],
        "scripts": ["scripts/sw_document_data.py", "scripts/sw_tolerance_extract.py", "scripts/sw_file_rename.py"],
        "summary": "配置与属性读写；改名默认只出预览，不直接改引用。",
    },
    {
        "id": "point_prompt",
        "label": "Point & Prompt：悬停/点选、Pin、白名单参数修改",
        "capabilities": [
            "solidworks_point_prompt_hover_selection",
            "solidworks_point_prompt_selection_pin",
            "solidworks_point_prompt_selection_context",
            "solidworks_point_prompt_feature_resolution",
            "solidworks_point_prompt_inline_prompt",
            "solidworks_point_prompt_edit_plan_preview",
            "solidworks_point_prompt_parametric_edit_apply",
            "solidworks_point_prompt_edit_verify_rollback",
        ],
        "tools": [
            "solidworks_get_pinned_context",
            "solidworks_pin_selection",
            "solidworks_unpin_selection",
            "solidworks_resolve_feature_candidates",
            "solidworks_preview_edit_plan",
            "solidworks_apply_edit_plan",
            "solidworks_verify_edit_result",
            "solidworks_undo_last_ai_operation",
            "solidworks_highlight_entities",
            "solidworks_get_selection_history",
        ],
        "scripts": ["scripts/sw_selection_context.py", "scripts/sw_apply_plan.py", "scripts/sw_edit_plan.py"],
        "summary": "Add-in 提供 Hover/Pin；MCP 只执行白名单参数修改并回读，失败回滚。",
    },
    {
        "id": "fea",
        "label": "仿真：CalculiX 线性/非线性、网格收敛",
        "capabilities": ["simulation_and_fea"],
        "tools": [
            "cadstudio_fea_preflight",
            "cadstudio_prepare_fea",
            "cadstudio_run_fea",
            "cadstudio_run_fea_convergence",
        ],
        "scripts": ["scripts/fea_analysis.py", "scripts/fea_convergence.py"],
        "summary": "生成 .inp、运行白名单求解器并审计收敛；结果必须工程复核。",
    },
    {
        "id": "routing",
        "label": "Routing：端点/分段/弯曲半径/碰撞复核",
        "capabilities": ["routing"],
        "tools": ["cadstudio_check_routing", "cadstudio_routing_preflight"],
        "scripts": ["scripts/routing_review.py"],
        "summary": "只做中性复核；原生 Routing 写入需要加载项与许可证证据。",
    },
    {
        "id": "open_format",
        "label": "无 CAD 开放格式：STEP/IGES/BREP/STL/OBJ/GLB/DXF/SVG/PDF",
        "capabilities": ["headless_open_format_writing", "surface_modeling", "mold_tools", "dxf_preview_scene"],
        "tools": [
            "cadstudio_write_open_format",
            "cadstudio_create_ocp_loft",
            "cadstudio_create_ocp_surface",
            "cadstudio_review_advanced_geometry",
            "cadstudio_build_dxf_preview_scene",
        ],
        "scripts": ["scripts/headless_cad_writer.py", "scripts/advanced_geometry_ocp.py", "scripts/advanced_surface_ocp.py"],
        "summary": "不需要 SolidWorks；不能生成或验证原生 SLDPRT/SLDASM/SLDDRW 特征树。",
    },
    {
        "id": "platform",
        "label": "平台：连接、后端路由、API 查证、Add-in 诊断、能力地图",
        "capabilities": ["requirements_planning", "solidworks_addin_host"],
        "tools": [
            "solidworks_connect",
            "solidworks_recover",
            "solidworks_health_check",
            "solidworks_addin_host_status",
            "solidworks_lookup_api",
            "cadstudio_resolve_backend",
        ],
        "scripts": ["scripts/sw_connect.py", "scripts/backend_router.py", "scripts/sw_api_lookup.py", "scripts/sw_preflight.py"],
        "summary": "先连接与自检，再按能力地图决定用 MCP 工具还是脚本；不要用临时 probe 脚本猜能力。",
    },
)

VERSION_SUPPORT = {
    "2024": {"level": "verified", "note": "仓库基线版本，已有真机回归"},
    "2025": {"level": "pilot", "note": "可连接与运行；升级到 verified 需要逐能力真机证据"},
    "2026": {"level": "verified", "note": "当前主要真机验收版本"},
}


def load_capabilities(path: str | Path | None = None) -> dict[str, Any]:
    """读取能力清单（JSON 结构的 YAML）。"""
    target = Path(path) if path else CAPABILITIES_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _level_rank(level: str) -> int:
    return {"verified": 0, "pilot": 1, "reference_only": 2, "not_implemented": 3}.get(str(level), 4)


def _worst_level(levels: list[str]) -> str:
    if not levels:
        return "not_implemented"
    return sorted(levels, key=_level_rank)[-1]


def build_map(
    *,
    domain: str | None = None,
    include_tools: bool = True,
    capabilities_path: str | Path | None = None,
    available_tools: set[str] | None = None,
) -> dict[str, Any]:
    """构建能力地图；``domain`` 为域 id，未知域抛 ValueError。"""
    data = load_capabilities(capabilities_path)
    index = {str(item["id"]): item for item in data.get("capabilities", [])}
    tools_available = set(available_tools or ())

    domains: list[dict[str, Any]] = []
    for spec in DOMAINS:
        if domain and spec["id"] != domain:
            continue
        caps = []
        for capability_id in spec["capabilities"]:
            item = index.get(capability_id)
            if item is None:
                continue
            caps.append(
                {
                    "id": capability_id,
                    "label": item.get("label", capability_id),
                    "level": item.get("level", "not_implemented"),
                    "verifiedVersions": item.get("verified_versions", []),
                }
            )
        tools = [name for name in spec["tools"] if not tools_available or name in tools_available]
        entry: dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "summary": spec["summary"],
            "level": _worst_level([cap["level"] for cap in caps]),
            "capabilities": caps,
            "scripts": list(spec["scripts"]),
        }
        if include_tools:
            entry["tools"] = tools
            if tools_available:
                entry["missingTools"] = [name for name in spec["tools"] if name not in tools_available]
        domains.append(entry)

    if domain and not domains:
        known = ", ".join(item["id"] for item in DOMAINS)
        raise ValueError(f"未知能力域: {domain}；可用域: {known}")

    unsupported = [
        {"id": item["id"], "label": item.get("label", item["id"]), "level": item.get("level")}
        for item in data.get("capabilities", [])
        if item.get("level") == "not_implemented"
    ]

    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "capabilities.yaml",
        "summary": {
            "domains": len(domains),
            "capabilities": len(index),
            "levels": _count_levels(data.get("capabilities", [])),
        },
        "versionSupport": VERSION_SUPPORT,
        "domains": domains,
        "unsupported": unsupported,
        "notes": [
            "能力等级来自 capabilities.yaml；pilot 表示已实现但需人工复核，not_implemented 表示尚不可用。",
            "不要为探测能力写临时 COM 脚本；先查本地图，再查 references/ 与 sw_capability_probe.py（只读）。",
            "SW2025 SP1 按 pilot 使用；逐能力真机验证后再升级等级。",
        ],
    }


def _count_levels(capabilities: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in capabilities:
        level = str(item.get("level") or "unknown")
        counts[level] = counts.get(level, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI：``python scripts/capability_map.py [--domain ID]``。"""
    import argparse

    parser = argparse.ArgumentParser(description="按工程域输出 SolidWorks 能力地图")
    parser.add_argument("--domain", default=None, help="能力域 id，例如 part_modeling")
    parser.add_argument("--no-tools", action="store_true", help="不列出 MCP 工具名")
    args = parser.parse_args(argv)
    print(json.dumps(build_map(domain=args.domain, include_tools=not args.no_tools), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
