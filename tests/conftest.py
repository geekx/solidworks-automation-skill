from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    """每个测试使用独立的桥接目录，避免污染真实 %LOCALAPPDATA%。"""
    monkeypatch.setenv("CADSTUDIO_POINT_PROMPT_DIR", str(tmp_path / "bridge"))
    import sw_cad_bridge

    sw_cad_bridge.ensure_bridge_dir()
    return sw_cad_bridge
