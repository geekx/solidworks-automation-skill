"""从本机登记且签名有效的 SolidWorks 安装源恢复对应 API Help。"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


def _registered_source(executable: Path, year: int) -> tuple[Path, str, str] | None:
    if os.name != "nt":
        return None
    import winreg
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in roots:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as root:
                    index = 0
                    while True:
                        try:
                            name = winreg.EnumKey(root, index)
                            index += 1
                        except OSError:
                            break
                        if not re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", name):
                            continue
                        try:
                            with winreg.OpenKey(root, name) as entry:
                                display = winreg.QueryValueEx(entry, "DisplayName")[0]
                                location = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                source = winreg.QueryValueEx(entry, "InstallSource")[0]
                                version = winreg.QueryValueEx(entry, "DisplayVersion")[0]
                            if not re.fullmatch(rf"SOLIDWORKS\s+{year}(?:\s+SP\s*[\d.]+)?", str(display), re.I):
                                continue
                            if Path(str(location)).resolve() != executable.parent.resolve():
                                continue
                            source_path = Path(str(source))
                            # 不自动访问 UNC 或网络驱动器，以免泄露凭据。
                            if not source_path.is_absolute() or str(source_path).startswith("\\\\"):
                                continue
                            source_path = source_path.resolve()
                            if str(source_path).startswith("\\\\"):
                                continue
                            drive = source_path.anchor
                            if not drive or ctypes.windll.kernel32.GetDriveTypeW(drive) not in {2, 3, 5}:
                                continue
                            msi = source_path / "solidworks.msi"
                            if msi.is_file() and msi.resolve().parent == source_path:
                                return msi, name.upper(), str(version)
                        except (OSError, ValueError):
                            continue
            except OSError:
                continue
    return None


def _signed_by_solidworks(msi: Path) -> bool:
    script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:SW_API_MSI; "
              "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch "
              "'O=Dassault Systemes SolidWorks Corp') { exit 2 }")
    try:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        modules = powershell.parent / "Modules"
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                                env={**os.environ, "SW_API_MSI": str(msi), "PSModulePath": str(modules)}, capture_output=True, timeout=25,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _msi_rows(msi_path: Path, query: str, columns: int) -> list[list[str]]:
    api = ctypes.WinDLL("msi")
    db = wintypes.UINT()
    if api.MsiOpenDatabaseW(str(msi_path), None, ctypes.byref(db)) != 0:
        raise OSError("无法打开安装包")
    view = wintypes.UINT()
    try:
        if api.MsiDatabaseOpenViewW(db, query, ctypes.byref(view)) != 0 or api.MsiViewExecute(view, 0) != 0:
            raise OSError("无法读取安装包表")
        result: list[list[str]] = []
        while True:
            record = wintypes.UINT()
            if api.MsiViewFetch(view, ctypes.byref(record)) != 0:
                break
            try:
                row = []
                for position in range(1, columns + 1):
                    size = wintypes.DWORD(2048)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    if api.MsiRecordGetStringW(record, position, buffer, ctypes.byref(size)) != 0:
                        raise OSError("安装包表字段过长")
                    row.append(buffer.value)
                result.append(row)
            finally:
                api.MsiCloseHandle(record)
        return result
    finally:
        if view.value:
            api.MsiCloseHandle(view)
        api.MsiCloseHandle(db)


def _msi_metadata(msi: Path, product_code: str, product_version: str, filename: str) -> tuple[Path, str, list[int], int]:
    properties = dict(_msi_rows(msi, "SELECT `Property`, `Value` FROM `Property`", 2))
    if properties.get("ProductCode", "").upper() != product_code or properties.get("ProductVersion") != product_version:
        raise ValueError("安装源与当前产品或版本不一致")
    entries = _msi_rows(msi, "SELECT `File`, `FileName`, `Sequence`, `FileSize` FROM `File`", 4)
    matched = [row for row in entries if row[1].split("|")[-1].casefold() == filename.casefold()]
    if len(matched) != 1:
        raise ValueError("安装包没有唯一的目标帮助文件")
    key, _, sequence, file_size = matched[0]
    size = int(file_size)
    if size <= 0 or size > 100_000_000:
        raise ValueError("帮助文件大小超出限制")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        raise ValueError("安装包文件标识不安全")
    media = _msi_rows(msi, "SELECT `LastSequence`, `Cabinet` FROM `Media`", 2)
    eligible = sorted((int(last), name) for last, name in media if int(last) >= int(sequence))
    cabinet = eligible[0][1] if eligible else ""
    if not re.fullmatch(r"[A-Za-z0-9_~.-]+\.cab", cabinet, re.I):
        raise ValueError("安装包资源位置不可用")
    hashes = _msi_rows(msi, "SELECT `File_`, `HashPart1`, `HashPart2`, `HashPart3`, `HashPart4` FROM `MsiFileHash`", 5)
    matched_hash = [row for row in hashes if row[0] == key]
    if len(matched_hash) != 1:
        raise ValueError("安装包没有目标帮助文件哈希")
    return msi.parent / cabinet, key, [int(value) & 0xFFFFFFFF for value in matched_hash[0][1:]], size


def _extract_bounded(sevenzip: str, cabinet: Path, key: str, temporary: Path, output: Any,
                     expected_size: int, cancelled: Any) -> None:
    args = [sevenzip, "e", "-so", "-spd", str(cabinet), key]
    process = subprocess.Popen(args, stdout=output, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 60
    try:
        while process.poll() is None:
            if cancelled and cancelled():
                raise ValueError("任务已取消")
            if temporary.stat().st_size > expected_size:
                raise ValueError("帮助文件超出安装包记录的大小")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, 60)
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        if process.returncode != 0:
            raise ValueError("帮助文件提取失败")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _matches_installer_hash(path: Path, expected: list[int]) -> bool:
    class HashInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", wintypes.DWORD * 4)]
    info = HashInfo()
    info.size = ctypes.sizeof(info)
    try:
        return ctypes.WinDLL("msi").MsiGetFileHashW(str(path), 0, ctypes.byref(info)) == 0 and list(info.data) == expected
    except (OSError, AttributeError):
        return False


def restore_local_help(executable: Path, year: int, filename: str, sevenzip: str, *,
                       cancelled: Any = None) -> tuple[Path | None, str | None]:
    """只读已登记的本地安装源，校验后将单个帮助文件放在用户缓存。"""
    if filename.casefold() not in {"sldworksapi.chm", "swconst.chm"}:
        return None, "拒绝恢复不受支持的帮助文件。"
    source = _registered_source(executable, year)
    if source is None:
        return None, "未找到当前版本可用的本地安装源，继续使用其他资料。"
    msi, product_code, version = source
    try:
        if cancelled and cancelled():
            return None, "任务已取消，未恢复本地 API Help。"
        if not _signed_by_solidworks(msi):
            raise ValueError("安装包签名无法确认")
        cabinet, key, expected, size = _msi_metadata(msi, product_code, version, filename)
        if not cabinet.is_file() or cabinet.resolve().parent != msi.parent.resolve():
            raise ValueError("安装源缺少帮助文件资源包")
        hasher = hashlib.sha256()
        with msi.open("rb") as handle:
            while data := handle.read(1024 * 1024):
                hasher.update(data)
        digest = hasher.hexdigest()
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "solidworks-automation" / "api-help" / f"{year}-{digest}"
        target = root / filename
        if target.is_file():
            if target.stat().st_size == size and _matches_installer_hash(target, expected):
                return target, None
            raise ValueError("已存在的帮助缓存校验失败，未覆盖")
        root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="api-help-", suffix=".tmp", dir=root, delete=False) as output:
                temporary = Path(output.name)
                _extract_bounded(sevenzip, cabinet, key, temporary, output, size, cancelled)
                output.flush()
            if temporary.stat().st_size != size or not _matches_installer_hash(temporary, expected):
                raise ValueError("帮助文件与已签名安装包记录不一致")
            if cancelled and cancelled():
                return None, "任务已取消，未恢复本地 API Help。"
            # 不覆盖可能由其他实例或用户创建的文件。
            created = False
            try:
                with temporary.open("rb") as incoming, target.open("xb") as outgoing:
                    created = True
                    while data := incoming.read(1024 * 1024):
                        outgoing.write(data)
            except OSError:
                if created:
                    target.unlink(missing_ok=True)
                raise
            return target, None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except ValueError as exc:
        return None, f"无法从本地安装源验证 API Help：{str(exc)[:160]}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"无法从本地安装源验证 API Help：{type(exc).__name__}"
