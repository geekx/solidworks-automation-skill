"""读取本地 API Help 和持久化的公开文档片段，不保存任务数据。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    from .api_environment import ensure_sevenzip
    from .api_help_restore import _registered_source, restore_local_help
except ImportError:
    from api_environment import ensure_sevenzip
    from api_help_restore import _registered_source, restore_local_help


def _find_sevenzip() -> str | None:
    """优先使用 PATH；Windows 上也接受 7-Zip 安装程序登记的目录。"""
    # 7za 的独立版本不支持 CHM；必须使用带完整格式库的 7z。
    executable = shutil.which("7z")
    if executable:
        return executable
    if os.name != "nt":
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\7-Zip", 0, winreg.KEY_READ | view) as key:
                    for name in ("Path64", "Path", "Path32"):
                        try:
                            directory, _ = winreg.QueryValueEx(key, name)
                            if isinstance(directory, str):
                                candidate = Path(directory) / "7z.exe"
                                if candidate.is_file():
                                    return str(candidate)
                        except (OSError, ValueError):
                            continue
            except OSError:
                continue
    return None


def _cache_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return root / "solidworks-automation" / "api-reference-cache-v1"


def _cache_path(version: int, interface: str, member: str) -> Path:
    key = f"{version}:{interface.casefold()}:{member.casefold()}"
    return _cache_dir() / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def save_document(version: int, interface: str, member: str, document: dict[str, Any]) -> None:
    """只保存明确允许的公开字段；失败不影响主要查询。"""
    fields = ("status", "source", "version", "title", "text", "sha256", "retrieved_at", "provider", "version_evidence", "trust", "runtime_verified")
    data = {key: document[key] for key in fields if key in document}
    if not _valid_document(data, version, interface, member):
        return
    try:
        directory = _cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False)
        try:
            os.replace(name, _cache_path(version, interface, member))
        finally:
            if os.path.exists(name):
                os.unlink(name)
    except OSError:
        return


def _valid_document(data: Any, version: int, interface: str, member: str) -> bool:
    if not isinstance(data, dict) or data.get("status") != "found" or data.get("version") != version:
        return False
    text, title = data.get("text"), data.get("title")
    if not isinstance(text, str) or not 0 < len(text) <= 6000 or not isinstance(title, str) or len(title) > 200:
        return False
    if member.casefold() not in text.casefold() or interface.casefold() not in title.casefold():
        return False
    if data.get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return False
    if data.get("provider") not in {"solidworks-direct", "parallel-anonymous"}:
        return False
    try:
        from .parallel_api_lookup import _official_url
    except ImportError:
        from parallel_api_lookup import _official_url
    if not _official_url(data.get("source"), version, interface, member):
        return False
    try:
        timestamp = datetime.fromisoformat(data.get("retrieved_at", ""))
        return timestamp.tzinfo is not None
    except (ValueError, TypeError):
        return False


def load_document(version: int, interface: str, member: str) -> dict[str, Any] | None:
    try:
        path = _cache_path(version, interface, member)
        if path.stat().st_size > 32768:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_document(data, version, interface, member):
            return None
        data["cache_status"] = "previously_retrieved"
        data["trust"] = "untrusted_reference"
        data["runtime_verified"] = False
        return data
    except (OSError, ValueError, UnicodeError):
        return None


class _TitleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.hidden = 0
        self.title: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title": self.in_title = True
        if tag in {"script", "style"}: self.hidden += 1
        if tag in {"p", "br", "div", "tr", "li", "h1", "h2", "pre"}: self.text.append("\n")
        if tag in {"td", "th"}: self.text.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title": self.in_title = False
        if tag in {"script", "style"} and self.hidden: self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title: self.title.append(data)
        elif not self.hidden: self.text.append(data)


def read_local_help(executable: Path, version: int, interface: str, member: str, url: str, *,
                    allow_install: bool = False, warnings: list[str] | None = None,
                    cancelled: Any = None) -> dict[str, Any] | None:
    """只读取已识别安装目录的 API CHM，7-Zip 缺失时安全跳过。

    文件归属安装不保证其内部版本，结果必须标为未独立确认版本。
    """
    install = executable.parent
    primary = "swconst.chm" if interface.casefold().startswith("sw") else "sldworksapi.chm"
    candidates = [install / "api" / primary, install / "api" / "apihelp.chm", install / "lang" / "apihelp.chm"]
    archive = next((path for path in candidates if path.is_file()), None)
    restoring = archive is None
    if restoring:
        # Authenticode 信任链检查可能访问证书状态服务；离线模式不运行安装源验证。
        if not allow_install:
            if warnings is not None: warnings.append("未发现本地 API Help；离线或自动配置关闭时不验证和恢复安装源。")
            return None
        if os.environ.get("SW_API_LOOKUP_AUTO_INSTALL", "on").strip().lower() in {"off", "false", "0"}:
            if warnings is not None: warnings.append("未发现本地 API Help，自动恢复已关闭。")
            return None
        if _registered_source(executable, version) is None:
            if warnings is not None: warnings.append("未发现目标安装的本地 API Help 或匹配的本地安装源，将继续使用其他资料。")
            return None
    sevenzip = _find_sevenzip()
    if not sevenzip:
        sevenzip, error = ensure_sevenzip(allow_network=allow_install, cancelled=cancelled)
        if error and warnings is not None:
            warnings.append(error)
    if not sevenzip:
        return None
    if restoring:
        archive, error = restore_local_help(executable, version, primary, sevenzip, cancelled=cancelled)
        if error and warnings is not None: warnings.append(error)
        if archive is None: return None
    basename = Path(unquote(urlsplit(url).path)).name
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run([sevenzip, "l", "-slt", "-ba", str(archive)], stdout=output,
                           stderr=subprocess.DEVNULL, check=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if output.tell() > 8 * 1024 * 1024: return None
            output.seek(0)
            listing = output.read().decode("utf-8", errors="replace")
        files = [line[7:] for line in listing.splitlines() if line.startswith("Path = ")]
        names = (basename, f"{interface}__{member}.htm")
        matches = []
        for expected in names:
            matches = [item for item in files if item.replace("\\", "/").rsplit("/", 1)[-1].casefold() == expected.casefold()]
            if matches: break
        if len(matches) != 1 or matches[0].startswith(("-", "@")) or any(
            part in {".", ".."} for part in matches[0].replace("\\", "/").split("/")
        ): return None
        with tempfile.TemporaryFile() as output:
            subprocess.run([sevenzip, "e", "-so", "-y", str(archive), matches[0]], stdout=output,
                           stderr=subprocess.DEVNULL, check=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if output.tell() > 2 * 1024 * 1024: return None
            output.seek(0)
            html = output.read().decode("utf-8", errors="replace")
        parsed = _TitleText()
        parsed.feed(html)
        title = "".join(parsed.title).strip()[:200]
        lines = [line.rstrip() if line.strip() else "" for line in "".join(parsed.text).lstrip("\ufeff").splitlines()]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        pos = text.casefold().find(member.casefold())
        enum_page = interface.casefold().startswith("sw") and not interface.startswith("I")
        if (interface.casefold() not in title.casefold() or pos < 0 or
                (not enum_page and member.casefold() not in title.casefold())):
            return None
        if pos > 5000: text = text[max(0, pos - 800):]
        text = text[:6000]
        return {"status": "found", "source": str(archive), "version": version, "title": title,
                "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider": "local-help",
                "version_evidence": "signed_installer_product_and_file_hash" if restoring else "installation_directory_only",
                "trust": "untrusted_reference", "runtime_verified": False}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
