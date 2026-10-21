"""@brief 通过真实 MCP 注册工具验收 SW2026，只操作本轮创建的独立实例及文件。"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import subprocess
import pythoncom

ROOT = Path(sys.argv[1]).resolve()
OUTPUT = Path(sys.argv[2]).resolve()
OUTPUT.mkdir(parents=True, exist_ok=False)
os.environ.update(SW_MCP_OUTPUT_ROOT=str(OUTPUT), SW_MCP_QUEUE_DIR=str(OUTPUT / "queue"))
sys.path[:0] = [str(ROOT), str(ROOT / "mcp-server")]
from scripts.sw_connect import connect_solidworks, get_com_member as get, get_sw_version, new_document, save_document, open_document
from scripts.sw_assembly import add_component
from scripts.sw_export import export_to_step
from scripts.sw_process import solidworks_processes
from scripts.sw_review import save_preview
from scripts import sw_native_wire as native
import server

report = {"status": "failed", "cases": [], "cleanup": {}}
prior = solidworks_processes()
if prior is None or prior:
    raise SystemExit("已有用户 SolidWorks 实例，停止隔离写入测试")
sw = None
pid = None
owned = False
titles = set()
pythoncom.CoInitialize()

def remember(model):
    """@brief 记录本轮文档标题，包含保存前后的变化。"""
    titles.add(str(get(model, "GetTitle")))
    return model

def active(model):
    """@brief 只激活本轮创建的文档，并核对路径。"""
    from win32com.client import VARIANT
    error = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.ActivateDoc3(str(get(model, "GetTitle")), False, 0, error)
    assert str(get(get(sw, "ActiveDoc"), "GetPathName")) == str(get(model, "GetPathName"))

def save(model, path):
    """@brief 保存本轮文档并确认实际文件存在。"""
    assert Path(path).is_relative_to(OUTPUT)
    assert save_document(model, str(path)) and Path(path).is_file()
    remember(model)

def reopen(model, path):
    """@brief 关闭自己的文档并重新载入，验证落盘后的真实模型。"""
    sw.CloseDoc(str(get(model, "GetTitle")))
    return remember(open_document(sw, str(path), silent=True, raise_on_error=True))

def call(name, params, expected="ok"):
    """@brief 走宿主持锁入口，保留原生子进程和结果归一化的真实执行。"""
    print("CALL " + name, flush=True)
    result = json.loads(server.mcp._tool_manager._tools[name].fn(params))
    report["cases"].append({"tool": name, "result": result})
    (OUTPUT / "progress.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result.get("status") == expected, result
    assert result.get("manual_review_required") is True
    if name != "solidworks_import_step_part":
        assert result.get("save_requested") is False and "saved" not in result
    return result

def inspect(model):
    active(model)
    return call("solidworks_inspect_native_geometry", native.Target(expected_document_path=str(get(model, "GetPathName"))))

def build(model, spec, dry=False):
    active(model)
    return call("solidworks_build_round_sweeps", native.BuildInput(expected_document_path=str(get(model, "GetPathName")), spec_path=str(spec), dry_run=dry), expected="ready" if dry else "ok")

def volume(result):
    """@brief 根据实体读回值计算总体积。"""
    return sum(body["volume_mm3"] for body in result["bodies"])

def prefs():
    """@brief 获取本轮会暂时改变的全局导入选项。"""
    # @brief 数值由本机 2026 PIA 枚举实际读取，记录并验证三个导入选项均恢复。
    return {"interconnect": sw.GetUserPreferenceToggle(691), "multibody": sw.GetUserPreferenceToggle(220), "mapping": sw.GetUserPreferenceIntegerValue(579)}

def reject_native(request, reason):
    """@brief 真实 C# 子进程必须拒绝错误 PID 或错误目标，不能只测 Python 预检。"""
    from scripts.cad_installation import discover_installation
    install = Path(discover_installation("solidworks")["executable"]).parent
    interop = next(p for p in [install / "api/redist", install] if (p / "SolidWorks.Interop.sldworks.dll").is_file())
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/sw_native_wire.ps1"), "-InteropRoot", str(interop)], input=json.dumps(request), capture_output=True, encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    # @brief Windows PowerShell 错误格式器可能在英文单词中换行，比较时忽略排版空白。
    assert result.returncode != 0 and "".join(reason.split()) in "".join(result.stderr.split()), result.stderr
    report["cases"].append({"guard": reason, "status": "pass"})

try:
    sw, _, meta = connect_solidworks(version=2026, wait_seconds=60, visible=False, return_metadata=True)
    pid = int(get(sw, "GetProcessID"))
    owned = bool(meta["started_by_cad_studio"]) and pid not in prior
    assert owned, "无法证明实例属于本轮测试"
    report["environment"] = {**get_sw_version(sw), "process_id": pid}
    print(json.dumps(report["environment"]), flush=True)
    # @brief 等待新实例向 ROT 完成注册；不将未就绪的启动窗口算作工具回归。
    sw.Visible = True
    import win32com.client
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            attached = win32com.client.GetActiveObject("SldWorks.Application")
            assert int(get(attached, "GetProcessID")) == pid
            break
        except Exception:
            pythoncom.PumpWaitingMessages()
            time.sleep(.25)
    else:
        table = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        print("ROT=" + repr([m.GetDisplayName(ctx, None) for m in table.EnumRunning()]), flush=True)
        raise RuntimeError("自己的 SolidWorks 实例未完成 ROT 注册")
    table = pythoncom.GetRunningObjectTable()
    ctx = pythoncom.CreateBindCtx(0)
    print("SOLIDWORKS ROT=" + repr([n for m in table.EnumRunning() if "solidworks" in (n := m.GetDisplayName(ctx, None)).lower()]), flush=True)
    model = remember(new_document(sw, "part"))
    model.SetUserPreferenceIntegerValue(46, 0)
    original = OUTPUT / "native-built.sldprt"
    save(model, original)
    disk_before = hashlib.sha256(original.read_bytes()).hexdigest()
    spec = OUTPUT / "spec.json"
    spec.write_text(json.dumps({"sweeps": [
        {"name": "Straight", "control_points_mm": [[0, 0, 0], [10, 0, 0]], "diameter_mm": 1, "color": [1, .7, .2]},
        {"name": "Bezier", "control_points_mm": [[0, 20, 0], [5, 25, 0], [10, 25, 0], [15, 20, 0]], "diameter_mm": 1, "color": [.1, .7, 1]},
    ], "boxes": [{"name": "Block", "bounds_mm": [20, 20, 2, 30, 30, 7], "color": [.5, .6, .7]}]}), encoding="utf-8")
    build(model, spec, dry=True)
    assert len(get(model, "GetBodies2", -1, False) or []) == 0
    built = build(model, spec)
    assert len(built["bodies"]) == 3 and model.ForceRebuild3(False)
    rows = {row["name"]: row for row in built["created"]}
    assert abs(rows["Straight"]["path_length_mm"] - 10) < .002
    assert abs(rows["Bezier"]["path_length_mm"] - rows["Bezier"]["expected_length_mm"]) < .002
    bodies = {row["name"]: row for row in built["bodies"]}
    assert abs(bodies["Straight"]["volume_mm3"] - math.pi * .5 ** 2 * 10) < .002
    assert abs(bodies["Block"]["volume_mm3"] - 500) < .002
    assert max(abs(a-b) for a,b in zip(bodies["Block"]["bounds_mm"], [20,20,2,30,30,7])) < .002
    assert hashlib.sha256(original.read_bytes()).hexdigest() == disk_before, "构建未经授权自动保存"
    reject_native({"process_id": 2147483647, "source": str(original), "action": "inspect"}, "Requested process unavailable")
    reject_native({"process_id": pid, "source": str(OUTPUT / "wrong-target.sldprt"), "action": "inspect"}, "Active document target mismatch")
    assert len(get(model, "GetBodies2", -1, False) or []) == 3
    save_preview(model, OUTPUT / "native-built.bmp", width=1200, height=800)
    inspect(model)
    save(model, original)
    model = reopen(model, original)
    reopened = inspect(model)
    assert len(reopened["bodies"]) == 3 and abs(volume(reopened) - volume(built)) < .002
    step = OUTPUT / "native-export.step"
    assert export_to_step(model, str(step)) and step.is_file()
    guard = remember(new_document(sw, "part"))
    guard.SetUserPreferenceIntegerValue(46, 0)
    guard_path = OUTPUT / "unrelated.sldprt"
    save(guard, guard_path)
    imported_path = OUTPUT / "native-imported.sldprt"
    active(guard)
    before_import_options = prefs()
    imported = call("solidworks_import_step_part", native.ImportInput(expected_document_path=str(guard_path), step_path=str(step), output_path=str(imported_path)))
    assert prefs() == before_import_options, "导入没有恢复全局选项"
    imported_model = remember(get(sw, "ActiveDoc"))
    assert imported["saved"] is True and imported["save_requested"] is True
    assert Path(str(get(imported_model, "GetPathName"))) == imported_path
    assert len(imported["bodies"]) == 3 and abs(volume(imported) - volume(built)) < .02
    report["import_options_restored"] = True
    imported_model = reopen(imported_model, imported_path)
    inspected_import = inspect(imported_model)
    exclude_name = inspected_import["bodies"][0]["name"]
    imported_hash = hashlib.sha256(imported_path.read_bytes()).hexdigest()
    excluded = call("solidworks_exclude_reviewed_bodies", native.DeleteInput(expected_document_path=str(imported_path), body_names=[exclude_name]))
    assert len(excluded["bodies"]) == 2
    assert hashlib.sha256(imported_path.read_bytes()).hexdigest() == imported_hash
    save(imported_model, imported_path)
    imported_model = reopen(imported_model, imported_path)
    assert len(inspect(imported_model)["bodies"]) == 2
    guard_spec = OUTPUT / "guard-spec.json"
    guard_spec.write_text(json.dumps({"boxes": [{"name": "Unrelated", "bounds_mm": [0,0,0,5,5,5]}]}), encoding="utf-8")
    build(guard, guard_spec)
    save(guard, guard_path)
    asm = remember(new_document(sw, "assembly"))
    first = add_component(asm, str(original), x=.04, y=.01, z=.02, sw=sw)
    assert first is not None and bool(get(first, "IsFixed"))
    second = add_component(asm, str(guard_path), x=.08, y=.04, z=.01, sw=sw)
    assert second is not None
    asm_path = OUTPUT / "replacement.sldasm"
    save(asm, asm_path)
    state = inspect(asm)
    old_name = str(get(first, "Name2"))
    old_row = next(c for c in state["components"] if c["name"] == old_name)
    asm_hash = hashlib.sha256(asm_path.read_bytes()).hexdigest()
    result = call("solidworks_replace_component_preserving_transform", native.ReplaceInput(expected_document_path=str(asm_path), component_name=old_name, replacement_path=str(imported_path)))
    current = next(c for c in result["components"] if c["path"].lower() == str(imported_path).lower())
    assert result["transform_preserved"] is True and current["fixed_component"] is True
    assert max(abs(a-b) for a,b in zip(current["transform"], old_row["transform"])) < 1e-10
    assert hashlib.sha256(asm_path.read_bytes()).hexdigest() == asm_hash
    save(asm, asm_path)
    asm = reopen(asm, asm_path)
    final = inspect(asm)
    restored = next(c for c in final["components"] if c["path"].lower() == str(imported_path).lower())
    assert restored["fixed_component"] and max(abs(a-b) for a,b in zip(restored["transform"], old_row["transform"])) < 1e-10
    save_preview(asm, OUTPUT / "replacement.bmp", width=1200, height=800)
    report["status"] = "pass"
except Exception as exc:
    report["error"] = f"{type(exc).__name__}: {exc}"
    traceback.print_exc()
finally:
    if sw is not None:
        try:
            closed = []
            for doc in list(get(sw, "GetDocuments") or []):
                title, path = str(get(doc, "GetTitle")), str(get(doc, "GetPathName") or "")
                if (path and Path(path).resolve().is_relative_to(OUTPUT)) or (not path and title in titles):
                    sw.CloseDoc(title)
                    closed.append(title)
            remaining = len(get(sw, "GetDocuments") or [])
            report["cleanup"] = {"closed_owned_documents": closed, "remaining_documents": remaining}
            if owned and remaining == 0:
                sw.ExitApp()
                report["cleanup"]["owned_instance_exited"] = True
        except Exception as exc:
            report["cleanup"]["error"] = str(exc)
    pythoncom.CoUninitialize()
    (OUTPUT / "native-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "error": report.get("error"), "cleanup": report["cleanup"]}, ensure_ascii=False), flush=True)
raise SystemExit(0 if report["status"] == "pass" else 1)
