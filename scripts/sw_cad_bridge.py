"""Point & Prompt 桥接目录（Add-in ↔ MCP 的文件 IPC）。

两侧只交换 JSON，不交换 COM 对象。写入一律「临时文件 + 原子替换」，读取对
`FileNotFound`、空文件和半写 JSON 做有限重试，避免 Add-in 与 MCP 同时读写时
读到截断内容。

目录规则与 `docs/point-and-prompt/interaction-spec.md` 第 2 节保持一致。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = "1.0"
DEFAULT_DIR_ENV = "CADSTUDIO_POINT_PROMPT_DIR"
_DIR_LOCK = threading.RLock()

CONTEXT_FILE = "context.json"
STATE_FILE = "state.json"
HISTORY_FILE = "history.jsonl"
PROMPT_REQUESTS_FILE = "prompt-requests.jsonl"
JOURNAL_FILE = "journal.jsonl"
HIGHLIGHTS_FILE = "highlights.json"
PLAN_PREVIEW_DIR = "plan-previews"


class BridgeError(RuntimeError):
    """桥接目录读写失败。"""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def bridge_dir() -> Path:
    """返回桥接目录；可用环境变量覆盖以便测试与多实例隔离。"""
    override = os.environ.get(DEFAULT_DIR_ENV, "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "CAD Studio" / "SolidWorksAddin" / "point-and-prompt"


def ensure_bridge_dir(directory: Path | None = None) -> Path:
    """创建桥接目录并返回。"""
    target = Path(directory) if directory else bridge_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / PLAN_PREVIEW_DIR).mkdir(parents=True, exist_ok=True)
    return target


def utc_now() -> str:
    """RFC 3339 UTC 时间戳（秒精度，Z 结尾）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, text: str) -> None:
    ensure_bridge_dir(path.parent)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(5):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            # Windows 上目标文件被短暂占用时重试；超过重试次数直接失败。
            if attempt == 4:
                try:
                    temp.unlink(missing_ok=True)
                finally:
                    raise
            time.sleep(0.02 * (attempt + 1))


def read_json(name: str, default: Any = None, *, attempts: int = 3) -> Any:
    """读取桥接 JSON 文件；缺失返回 default，损坏则重试后抛 BridgeError。"""
    path = bridge_dir() / name
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise json.JSONDecodeError("empty file", text, 0)
            return json.loads(text)
        except FileNotFoundError:
            return default
        except (json.JSONDecodeError, OSError) as exc:  # 半写文件在下次读取时通常已修复
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    raise BridgeError("E_BRIDGE_UNREADABLE", f"无法读取桥接文件 {path}：{last_error}") from last_error


def write_json(name: str, payload: Any) -> Path:
    """原子写入桥接 JSON 文件。"""
    path = ensure_bridge_dir() / name
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    return path


def append_jsonl(name: str, record: dict[str, Any], *, max_bytes: int = 32 * 1024 * 1024) -> Path:
    """追加一条 JSONL 记录，超过上限时轮转为 ``<name>.1``。"""
    path = ensure_bridge_dir() / name
    with _DIR_LOCK:
        try:
            if path.exists() and path.stat().st_size > max_bytes:
                rotated = path.with_name(path.name + ".1")
                os.replace(path, rotated)
        except OSError:
            pass
        line = json.dumps(record, ensure_ascii=False, sort_keys=False)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return path


def read_jsonl(name: str, *, limit: int | None = None, tail: bool = False) -> list[dict[str, Any]]:
    """读取 JSONL；跳过无法解析的残行，按写入顺序返回。"""
    path = bridge_dir() / name
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    if limit is not None and limit >= 0:
        return records[-limit:] if tail else records[:limit]
    return records


def read_state() -> dict[str, Any]:
    """读取桥接状态；缺失时返回最小骨架。"""
    state = read_json(STATE_FILE, default=None)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schemaVersion", SCHEMA_VERSION)
    state.setdefault("bridgeRevision", 0)
    state.setdefault("pinned", None)
    state.setdefault("addin", {"status": "unknown"})
    state.setdefault("hover", {"status": "unknown"})
    return state


def update_state(**changes: Any) -> dict[str, Any]:
    """更新桥接状态字段并递增 bridgeRevision。"""
    with _DIR_LOCK:
        state = read_state()
        state.update(changes)
        state["schemaVersion"] = SCHEMA_VERSION
        state["bridgeRevision"] = int(state.get("bridgeRevision", 0)) + 1
        state["updatedAt"] = utc_now()
        write_json(STATE_FILE, state)
        return state


def set_pinned(document_token: str | None, selection_token: str | None, entity_tokens: Iterable[str] = ()) -> dict[str, Any]:
    """记录/清除 Pin 段；清除时 reason 由调用方先写历史。"""
    pinned = None
    if document_token and selection_token:
        pinned = {
            "documentToken": document_token,
            "selectionToken": selection_token,
            "entityTokens": [str(token) for token in entity_tokens],
            "pinnedAt": utc_now(),
        }
    return update_state(pinned=pinned)


def write_context(context: dict[str, Any]) -> Path:
    """写入 SelectionContext，并同步 bridgeRevision。"""
    path = write_json(CONTEXT_FILE, context)
    with _DIR_LOCK:
        state = read_state()
        state["bridgeRevision"] = int(state.get("bridgeRevision", 0)) + 1
        state["updatedAt"] = utc_now()
        write_json(STATE_FILE, state)
    return path


def read_context(*, allow_stale: bool = True) -> dict[str, Any] | None:
    """读取 SelectionContext；``allow_stale=False`` 时仅返回新鲜上下文。"""
    context = read_json(CONTEXT_FILE, default=None)
    if not isinstance(context, dict):
        return None
    if allow_stale:
        return context
    staleness = context.get("staleness") or {}
    return context if staleness.get("status", "fresh") == "fresh" else None


def clear_context() -> None:
    """删除上下文文件（文档关闭/失效时）。"""
    with _DIR_LOCK:
        try:
            (bridge_dir() / CONTEXT_FILE).unlink()
        except FileNotFoundError:
            pass


def append_history(kind: str, **fields: Any) -> Path:
    """追加交互历史。"""
    record = {"at": utc_now(), "kind": str(kind)}
    record.update(fields)
    return append_jsonl(HISTORY_FILE, record)


def read_history(*, limit: int = 50) -> list[dict[str, Any]]:
    """读取最近的交互历史。"""
    return read_jsonl(HISTORY_FILE, limit=limit, tail=True)


def append_prompt_request(prompt: str, *, context: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
    """记录用户在 TaskPane 发出的 Prompt 请求。"""
    record = {
        "requestId": request_id or f"req-{int(time.time() * 1000)}",
        "at": utc_now(),
        "prompt": str(prompt),
        "context": {
            "documentToken": (context or {}).get("document", {}).get("documentToken"),
            "selectionToken": (context or {}).get("selection", {}).get("selectionToken"),
            "entityToken": (context or {}).get("selection", {}).get("activeEntityToken"),
            "revision": (context or {}).get("modelState", {}).get("revision"),
        },
        "status": "pending",
    }
    append_jsonl(PROMPT_REQUESTS_FILE, record)
    return record


def read_prompt_requests(*, limit: int = 20) -> list[dict[str, Any]]:
    """读取最近 Prompt 请求。"""
    return read_jsonl(PROMPT_REQUESTS_FILE, limit=limit, tail=True)


def write_plan_preview(plan_id: str, payload: dict[str, Any]) -> Path:
    """写入计划卡片供 Add-in 渲染。"""
    safe_id = "".join(ch for ch in str(plan_id) if ch.isalnum() or ch in "._-") or "plan"
    path = ensure_bridge_dir() / PLAN_PREVIEW_DIR / f"{safe_id}.json"
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def read_plan_preview(plan_id: str) -> dict[str, Any] | None:
    """读取指定计划卡片。"""
    safe_id = "".join(ch for ch in str(plan_id) if ch.isalnum() or ch in "._-") or "plan"
    payload = read_json(str(Path(PLAN_PREVIEW_DIR) / f"{safe_id}.json"), default=None)
    return payload if isinstance(payload, dict) else None


def list_plan_previews(*, limit: int = 20) -> list[dict[str, Any]]:
    """按修改时间列出最近计划卡片内容。"""
    directory = ensure_bridge_dir() / PLAN_PREVIEW_DIR
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    items: list[dict[str, Any]] = []
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def write_highlight_request(entity_tokens: Iterable[str], *, color: str = "#FF8C00", ttl_seconds: int = 30) -> Path:
    """请求 Add-in 高亮实体。"""
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "requestedAt": utc_now(),
        "expiresAt": datetime.fromtimestamp(time.time() + max(1, ttl_seconds), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "color": color,
        "entityTokens": [str(token) for token in entity_tokens],
    }
    return write_json(HIGHLIGHTS_FILE, payload)


def document_revision(document_token: str) -> int:
    """读取某文档的模型 revision 计数（由写执行器维护）。"""
    state = read_state()
    revisions = state.get("documentRevisions") or {}
    try:
        return int(revisions.get(document_token, 0))
    except (TypeError, ValueError):
        return 0


def bump_document_revision(document_token: str) -> int:
    """写入成功后递增文档 revision 并返回新值。"""
    with _DIR_LOCK:
        state = read_state()
        revisions = dict(state.get("documentRevisions") or {})
        current = int(revisions.get(document_token, 0)) + 1
        revisions[document_token] = current
        state["documentRevisions"] = revisions
        state["updatedAt"] = utc_now()
        write_json(STATE_FILE, state)
    return current


def append_journal(record: dict[str, Any]) -> Path:
    """追加 OperationJournal 记录。"""
    return append_jsonl(JOURNAL_FILE, record)


def read_journal(*, limit: int = 50) -> list[dict[str, Any]]:
    """读取最近的 Journal 记录。"""
    return read_jsonl(JOURNAL_FILE, limit=limit, tail=True)


def iter_journal(*, since_entry_id: str | None = None) -> Iterator[dict[str, Any]]:
    """按顺序迭代 Journal，可在给定 entryId 之后开始。"""
    started = since_entry_id is None
    for record in read_jsonl(JOURNAL_FILE):
        if not started:
            if record.get("entryId") == since_entry_id:
                started = True
            continue
        yield record
