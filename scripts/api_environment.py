"""API 查证运行环境：按需安装缺失组件，保留离线与失败回退。"""
from __future__ import annotations

import hashlib
import importlib
import os
import platform
import site
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

_LOCK = threading.Lock()
_FAILED_UNTIL: dict[str, float] = {}
_OFF = {"off", "false", "0"}
_RELEASE = "https://github.com/ip7z/7zip/releases/download/26.05/"
_SEVENZIP_FILES = {
    "7zr.exe": "9bf582b894dfa665f852d7e9112b02290a1c303c2d95a21fcdc41ffcb03143b7",
    "7z2605-x64.exe": "fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0",
    "7z.exe": "94205cfc054dd3bbeb978905bc619a65b3f04e9e73bb2717841110b6f46d962e",
    "7z.dll": "78bf6b668d75801e3aa180b4ccb16e6d8395fba80dec8a065055bb6f8dfabb82",
}


class SetupCancelled(Exception):
    """用户取消了自动配置。"""


@contextmanager
def _setup_lock(cancelled: Callable[[], bool] | None):
    """同时串行化同进程线程和不同客户端进程中的安装。"""
    deadline = time.monotonic() + 200
    while not _LOCK.acquire(timeout=0.2):
        if (cancelled and cancelled()) or time.monotonic() >= deadline:
            yield False
            return
    handle = None
    locked = False
    try:
        try:
            root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "solidworks-automation"
            root.mkdir(parents=True, exist_ok=True)
            handle = (root / "api-setup.lock").open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            while not locked:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    if (cancelled and cancelled()) or time.monotonic() >= deadline:
                        break
                    time.sleep(0.2)
        except OSError:
            locked = False
        yield locked
    finally:
        try:
            if handle is not None:
                try:
                    if locked:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            _LOCK.release()


def _run_setup(args: list[str], timeout: float, cancelled: Callable[[], bool] | None) -> None:
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancelled and cancelled():
                raise SetupCancelled()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def setup_enabled(allow_network: bool) -> bool:
    return (allow_network and os.environ.get("SW_API_LOOKUP_NETWORK", "on").strip().lower() not in _OFF
            and os.environ.get("SW_API_LOOKUP_AUTO_INSTALL", "on").strip().lower() not in _OFF)


def _missing(modules: tuple[str, ...]) -> bool:
    for name in modules:
        try:
            importlib.import_module(name)
        except (ImportError, OSError):
            return True
    return False


def ensure_python_packages(requirements: tuple[str, ...], modules: tuple[str, ...], *,
                           allow_network: bool, cancelled: Callable[[], bool] | None = None) -> str | None:
    """缺失时只安装指定依赖；失败返回可见错误，避免重复安装。"""
    if not _missing(modules):
        return None
    if not setup_enabled(allow_network):
        return "缺少 Python 依赖且自动安装已关闭：" + ", ".join(requirements)
    key = "pip:" + ",".join(requirements)
    with _setup_lock(cancelled) as acquired:
        if not acquired:
            return "自动配置锁不可用或任务已取消，未安装 Python 依赖。"
        if not _missing(modules):
            return None
        if time.monotonic() < _FAILED_UNTIL.get(key, 0):
            return "Python 依赖安装近期失败，请检查网络或 Python 环境：" + ", ".join(requirements)
        if cancelled and cancelled():
            return "任务已取消，未安装 Python 依赖。"
        args = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                "--only-binary=:all:"]
        install_to_user = sys.prefix == getattr(sys, "base_prefix", sys.prefix)
        if install_to_user:
            args.append("--user")
        args.extend(requirements)
        try:
            _run_setup(args, 180, cancelled)
            importlib.invalidate_caches()
            if install_to_user and not sys.flags.no_user_site:
                site.addsitedir(site.getusersitepackages())
            if not _missing(modules):
                return None
        except SetupCancelled:
            return "任务已取消，已停止 Python 依赖安装。"
        except (OSError, subprocess.SubprocessError):
            pass
        _FAILED_UNTIL[key] = time.monotonic() + 300
        return "Python 依赖自动安装失败，请检查网络、pip 和写入权限：" + ", ".join(requirements)


def _portable_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "solidworks-automation" / "tools" / "7zip-26.05-x64"


def _valid_portable(root: Path) -> bool:
    try:
        return all((root / name).stat().st_size <= 10_000_000 and
                   hashlib.sha256((root / name).read_bytes()).hexdigest() == _SEVENZIP_FILES[name]
                   for name in ("7z.exe", "7z.dll"))
    except OSError:
        return False


def _download(url: str, expected: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "SolidWorks-API-Lookup/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("7-Zip 下载内容与官方版本哈希不一致")
    destination.write_bytes(data)


def ensure_sevenzip(*, allow_network: bool, cancelled: Callable[[], bool] | None = None) -> tuple[str | None, str | None]:
    """仅安装经过固定官方哈希验证的 x64 便携版，不更改系统安装或 PATH。"""
    root = _portable_root()
    if _valid_portable(root):
        return str(root / "7z.exe"), None
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        return None, "未找到可用的 7-Zip；此平台尚不支持自动配置本地帮助解析器。"
    if not setup_enabled(allow_network):
        return None, "未找到 7-Zip，且离线模式或设置禁止自动安装。"
    with _setup_lock(cancelled) as acquired:
        if not acquired:
            return None, "自动配置锁不可用或任务已取消，未安装 7-Zip。"
        if _valid_portable(root):
            return str(root / "7z.exe"), None
        if root.exists():
            return None, "已存在的便携版 7-Zip 校验失败；未覆盖该目录。"
        if time.monotonic() < _FAILED_UNTIL.get("7zip", 0):
            return None, "7-Zip 自动配置近期失败，请检查网络后重试。"
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="7zip-setup-", dir=root.parent) as temp:
                work = Path(temp)
                for name in ("7zr.exe", "7z2605-x64.exe"):
                    if cancelled and cancelled():
                        return None, "任务已取消，未安装 7-Zip。"
                    _download(_RELEASE + name, _SEVENZIP_FILES[name], work / name)
                out = work / "portable"
                out.mkdir()
                _run_setup([str(work / "7zr.exe"), "e", "-y", "-o" + str(out),
                            str(work / "7z2605-x64.exe"), "7z.exe", "7z.dll"], 30, cancelled)
                if not _valid_portable(out):
                    raise ValueError("7-Zip 提取文件校验失败")
                try:
                    out.rename(root)
                except OSError:
                    if not _valid_portable(root):
                        raise
            return str(root / "7z.exe"), None
        except SetupCancelled:
            return None, "任务已取消，已停止 7-Zip 自动配置。"
        except (OSError, ValueError, subprocess.SubprocessError):
            _FAILED_UNTIL["7zip"] = time.monotonic() + 300
            return None, "7-Zip 自动配置失败；可检查网络并手动安装官方 7-Zip。"
