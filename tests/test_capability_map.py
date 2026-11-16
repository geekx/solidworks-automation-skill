from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import capability_map as capability_map_module  # noqa: E402


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads((ROOT / "capabilities.yaml").read_text(encoding="utf-8"))


def test_map_lists_every_domain_and_matches_registry(registry):
    payload = capability_map_module.build_map()
    assert payload["summary"]["domains"] == len(capability_map_module.DOMAINS)
    assert payload["summary"]["capabilities"] == len(registry["capabilities"])

    index = {item["id"]: item for item in registry["capabilities"]}
    for domain in payload["domains"]:
        assert domain["capabilities"], domain["id"]
        for cap in domain["capabilities"]:
            assert cap["id"] in index, cap["id"]
            assert cap["level"] == index[cap["id"]]["level"]


def test_domain_filter_and_unknown_domain(registry):
    part = capability_map_module.build_map(domain="part_modeling")
    assert len(part["domains"]) == 1
    tools = part["domains"][0]["tools"]
    assert "solidworks_sketch_and_extrude" in tools
    assert "solidworks_fillet" in tools

    with pytest.raises(ValueError, match="未知能力域"):
        capability_map_module.build_map(domain="no_such_domain")


def test_point_prompt_domain_contains_the_tool_set(registry):
    payload = capability_map_module.build_map(domain="point_prompt")
    domain = payload["domains"][0]
    assert domain["level"] in {"pilot", "not_implemented"}
    tools = set(domain["tools"])
    for expected in (
        "solidworks_get_pinned_context",
        "solidworks_apply_edit_plan",
        "solidworks_verify_edit_result",
        "solidworks_undo_last_ai_operation",
        "solidworks_get_selection_history",
    ):
        assert expected in tools


def test_version_support_marks_2025_as_pilot():
    payload = capability_map_module.build_map()
    assert payload["versionSupport"]["2024"]["level"] == "verified"
    assert payload["versionSupport"]["2025"]["level"] == "pilot"
    assert payload["versionSupport"]["2026"]["level"] == "verified"


def test_unsupported_lists_not_implemented_capabilities(registry):
    payload = capability_map_module.build_map()
    unsupported = {item["id"] for item in payload["unsupported"]}
    expected = {item["id"] for item in registry["capabilities"] if item["level"] == "not_implemented"}
    assert unsupported == expected


def test_every_declared_tool_exists_in_server_source():
    sources = [
        (ROOT / "mcp-server" / "server.py").read_text(encoding="utf-8"),
        (ROOT / "scripts" / "sw_native_wire.py").read_text(encoding="utf-8"),
    ]
    registered = set()
    for text in sources:
        registered.update(re.findall(r'name="([a-z0-9_]+)"', text))
        registered.update(re.findall(r"name='([a-z0-9_]+)'", text))
    missing = []
    for domain in capability_map_module.DOMAINS:
        for tool in domain["tools"]:
            if tool not in registered:
                missing.append(f"{domain['id']}:{tool}")
    assert not missing, missing


def test_sheet_metal_and_gear_domains_have_tools_or_scripts():
    domains = {item["id"]: item for item in capability_map_module.DOMAINS}
    assert domains["sheet_metal"]["tools"] == ["solidworks_create_sheet_metal_flange"]
    assert "scripts/sw_sheet_metal.py" in domains["sheet_metal"]["scripts"]
    assert domains["standard_parts"]["tools"] == ["solidworks_create_gear"]
    # 焊件目前只有脚本，地图必须明确暴露这一点而不是消失。
    assert domains["weldments"]["tools"] == []
    assert "scripts/sw_weldment.py" in domains["weldments"]["scripts"]


def test_discovery_concepts_cover_new_domains():
    sys.path.insert(0, str(ROOT / "mcp-server"))
    import discovery  # noqa: E402

    terms = {term for group in discovery._CONCEPTS for term in group}
    for expected in ("钣金", "焊件", "齿轮", "能力地图", "基体法兰"):
        assert expected in terms, expected


def test_mcp_tool_returns_grouped_map():
    if str(ROOT / "mcp-server") not in sys.path:
        sys.path.insert(0, str(ROOT / "mcp-server"))
    import server  # noqa: E402

    payload = json.loads(
        server.solidworks_capability_map(server.SolidWorksCapabilityMapInput(domain="sheet_metal"))
    )
    assert payload["domains"][0]["id"] == "sheet_metal"
    assert "solidworks_create_sheet_metal_flange" in payload["domains"][0]["tools"]
    assert payload["versionSupport"]["2025"]["level"] == "pilot"
