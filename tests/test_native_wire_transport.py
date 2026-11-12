"""@brief 核对完整请求走标准输入，并验证目标拒绝和传输体积边界。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import cad_installation, sw_native_wire as native


@pytest.fixture
def native_host(monkeypatch, tmp_path):
    """@brief 提供本地安装与 COM 元数据桩，不启动 CAD 或加载模拟 DLL。"""
    install = tmp_path / "安装路径" / "SOLIDWORKS"
    redist = install / "api" / "redist"
    redist.mkdir(parents=True)
    (redist / "SolidWorks.Interop.sldworks.dll").touch()
    target = str(tmp_path / "中文零件.sldprt")
    model = SimpleNamespace()
    sw = SimpleNamespace()
    metadata = {(id(sw), "ActiveDoc"): model, (id(sw), "GetProcessID"): 123, (id(model), "GetPathName"): target}
    # @brief 模拟连接元数据访问器，使离线传输测试不依赖 Windows COM 模块。
    monkeypatch.setitem(sys.modules, "scripts.sw_connect", SimpleNamespace(get_com_member=lambda obj, name: metadata[(id(obj), name)]))
    monkeypatch.setattr(cad_installation, "discover_installation", lambda product: {"executable": str(install / "SLDWORKS.exe")})
    return sw, target


def test_maximum_high_precision_spec_uses_short_command_and_utf8_stdin(native_host, monkeypatch):
    """@brief 曾超过 32K 命令行的合法规格必须完整送达，包含最大条数与长名称。"""
    sw, target = native_host
    points = [[float(f"{i}.{axis}1234567890123") for axis in range(3)] for i in range(6)]
    spec = native.BuildSpec(
        sweeps=[{"name": "Wire" + str(i) + "x" * 90, "control_points_mm": points, "diameter_mm": .9} for i in range(64)],
        boxes=[{"name": "Box" + str(i), "bounds_mm": [0, 0, 0, 10, 10, 5]} for i in range(64)],
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert len(subprocess.list2cmdline(command)) < 2048
        assert len(kwargs["input"].encode("utf-8")) > 32767
        assert kwargs["encoding"] == "utf-8"
        payload = json.loads(kwargs["input"])
        assert payload["source"] == target
        assert payload["process_id"] == 123
        assert payload["spec"] == json.loads(spec.model_dump_json())
        return SimpleNamespace(returncode=0, stdout='{"status":"ready","save_requested":false}', stderr="")

    monkeypatch.setattr(native.subprocess, "run", run)
    result = native.invoke_native(sw, target, "build", dry_run=True, spec=spec.model_dump())
    assert result["status"] == "ready"
    assert len(calls) == 1


def test_oversize_request_is_rejected_before_child_launch(native_host, monkeypatch):
    """@brief 超过字节上限时不得启动子进程，限制应按 UTF-8 字节计算。"""
    sw, target = native_host
    monkeypatch.setattr(native.subprocess, "run", lambda *args, **kwargs: pytest.fail("Child must not start"))
    with pytest.raises(ValueError, match="exceeds 1 MiB"):
        native.invoke_native(sw, target, "exclude", body_names=["体" * (native.MAX_NATIVE_REQUEST_BYTES // 2)])


def test_target_mismatch_is_rejected_before_child_launch(native_host, monkeypatch):
    """@brief 改用标准输入后，原来的活动文档保护仍须有效。"""
    sw, target = native_host
    monkeypatch.setattr(native.subprocess, "run", lambda *args, **kwargs: pytest.fail("Child must not start"))
    with pytest.raises(ValueError, match="target mismatch"):
        native.invoke_native(sw, target + ".other", "inspect")


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows PowerShell pipe contract")
def test_real_powershell_receives_large_utf8_request(tmp_path):
    """@brief 用真实 PowerShell 读管道，独立核对超过命令行上限的中文 JSON。"""
    script = tmp_path / "管道验收.ps1"
    script.write_text(
        "[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)\n"
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)\n"
        "$request=[Console]::In.ReadToEnd()\n"
        "[Console]::Out.Write($request)\n", encoding="utf-8-sig",
    )
    request = json.dumps({"路径": str(tmp_path / "中文零件.sldprt"), "data": "数值" * 20000}, ensure_ascii=False)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        input=request, capture_output=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(request)
