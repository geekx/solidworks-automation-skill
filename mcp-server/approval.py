"""写操作的本地批准记录；模型传入的工具参数不能直接构造批准。

仅支持逐次批准：绑定工具名、完整参数摘要和调用方标识，一次性、有有效期。
范围授权暂不支持，既有范围记录也不会被消费。

落地形态是本地目录里的 JSON，签名密钥在同一目录生成。CLI 提供交互式确认，
但终端检查无法证明输入来自人；具有同一 OS 用户权限的程序可能读写账本和密钥，
HMAC 并不隔离这些程序。部署者应保护账本目录及执行环境；不能以此机制声称防止
具有同等本地权限的恶意进程伪造批准。工具参数中的 ``approved=true`` 不生效。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from policy import Effect, canonical_digest, classify, targets

DEFAULT_CALL_TTL_SECONDS = 15 * 60
MAX_APPROVAL_ARGUMENT_BYTES = 64 * 1024
# 历史记录不自动删除；满额时暂停新增并提示用户自行归档。
MAX_APPROVAL_REQUEST_RECORDS = 1000
MAX_APPROVAL_GRANT_RECORDS = 1000

# 请求 ID 由 token_hex(4) 生成；校验格式同时挡住 ../ 之类的路径注入。
_REQUEST_ID = re.compile(r"[0-9a-f]{8}")


def default_root() -> Path:
    """批准记录目录；放在用户数据区，不进仓库、不随安装包分发。"""
    override = os.environ.get("SW_MCP_APPROVAL_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "SolidWorksMCP" / "approvals"


class ApprovalError(RuntimeError):
    """批准记录不合法、已过期或已被使用。"""


def _locked(method):
    """跨进程保护账本的读取、校验与更新。"""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock():
            return method(self, *args, **kwargs)
    return wrapper


class ApprovalLedger:
    """批准请求与批准记录的本地账本。"""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else default_root()
        self.requests_dir = self.root / "requests"
        self.grants_dir = self.root / "grants"
        for path in (self.root, self.requests_dir, self.grants_dir):
            path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self):
        # 独立文件描述符使锁覆盖其他线程和其他服务进程；崩溃时由 OS 释放。
        with (self.root / '.lock').open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ApprovalError('审批账本锁超时，拒绝执行。') from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------ 密钥
    def _secret(self) -> bytes:
        path = self.root / "secret.key"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            try:
                path.chmod(0o600)
            except OSError:  # pragma: no cover - Windows ACL 由用户目录继承
                pass
        return path.read_bytes()

    def _sign(self, body: dict[str, Any]) -> str:
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._secret(), payload, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ 请求
    @_locked
    def request(self, tool: str, arguments: Any, effect: str, *, subject: str | None = None) -> dict[str, Any]:
        """登记一次待批准调用；同一工具同一参数重复请求会复用原 id。"""
        summary = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        if len(summary.encode('utf-8')) > MAX_APPROVAL_ARGUMENT_BYTES:
            raise ApprovalError('操作参数超过审批显示上限，拒绝执行。')
        digest = canonical_digest(tool, arguments)
        for existing in self.pending():
            if existing["tool"] == tool and existing["digest"] == digest and existing.get("subject") == subject:
                return existing
        if sum(1 for _ in self.requests_dir.glob("*.json")) >= MAX_APPROVAL_REQUEST_RECORDS:
            raise ApprovalError("审批请求记录已达容量上限；请在安全位置归档旧记录后重试。")
        # 短 ID 可能发生碰撞；不得覆盖已经存在的请求或旧授权。
        request_id = None
        for _ in range(32):
            candidate = secrets.token_hex(4)
            if not (self.requests_dir / f"{candidate}.json").exists() and not (self.grants_dir / f"{candidate}.json").exists():
                request_id = candidate
                break
        if request_id is None:
            raise ApprovalError("无法生成唯一的审批请求 ID，拒绝创建请求。")
        now = time.time()
        record = {
            "id": request_id,
            "kind": "call",
            "tool": tool,
            "subject": subject,
            "effect": effect,
            "digest": digest,
            "summary": summary,
            "targets": targets(arguments),
            "created_at": now,
            "expires_at": now + DEFAULT_CALL_TTL_SECONDS,
            "status": "pending",
        }
        self._write(self.requests_dir / f"{record['id']}.json", record)
        return record

    def pending(self) -> list[dict[str, Any]]:
        """未过期且仍在等待人工决定的请求。"""
        now = time.time()
        items = []
        for path in sorted(self.requests_dir.glob("*.json")):
            record = self._read(path)
            if record.get("status") == "pending" and record.get("expires_at", 0) > now:
                items.append(record)
        return items

    def decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的人工决定，便于用户确认自己批过什么。"""
        items = [self._read(path) for path in self.requests_dir.glob("*.json")]
        decided = [item for item in items if item.get("status") in {"approved", "denied"}]
        decided.sort(key=lambda item: item.get("decided_at") or 0, reverse=True)
        return decided[:limit]

    # ------------------------------------------------------------------ 人工决定
    def _request_path(self, request_id: str) -> Path:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ApprovalError(f"非法的请求 ID: {request_id!r}")
        return self.requests_dir / f"{request_id}.json"

    def request_record(self, request_id: str) -> dict[str, Any]:
        """按 ID 读取请求详情；ID 格式非法时报 ApprovalError。"""
        return self._read(self._request_path(request_id))

    @_locked
    def approve(self, request_id: str, ttl_seconds: int | None = None, *,
                expected_record: dict[str, Any] | None = None) -> dict[str, Any]:
        """把一条 pending 请求变成一次性 call 批准记录。"""
        record = self.request_record(request_id)
        if expected_record is not None and record != expected_record:
            raise ApprovalError(f"请求 {request_id} 在展示后发生变化，拒绝批准。")
        try:
            displayed_arguments = json.loads(record["summary"])
            matches_display = (
                record["digest"] == canonical_digest(record["tool"], displayed_arguments)
                and record.get("targets", []) == targets(displayed_arguments)
            )
        except (KeyError, TypeError, ValueError):
            matches_display = False
        if not matches_display:
            raise ApprovalError(f"请求 {request_id} 的参数摘要与执行内容不一致，拒绝批准。")
        effect, _ = classify(record["tool"], displayed_arguments)
        if effect not in (Effect.WRITE, Effect.DESTRUCTIVE) or record.get("effect") != effect.value:
            raise ApprovalError(f"请求 {request_id} 的副作用等级与当前策略不一致，拒绝批准。")
        if record.get("status") != "pending":
            raise ApprovalError(f"请求 {request_id} 当前状态是 {record.get('status')}，不能批准。")
        if record.get("expires_at", 0) <= time.time():
            raise ApprovalError(f"请求 {request_id} 已过期，重新调用工具即可生成新的请求。")
        # 容量检查必须在锁内、修改请求状态前完成，避免批准后无法签发授权。
        if sum(1 for _ in self.grants_dir.glob("*.json")) >= MAX_APPROVAL_GRANT_RECORDS:
            raise ApprovalError("审批授权记录已达容量上限；请在安全位置归档旧记录后重试。")
        record.update({"status": "approved", "decided_at": time.time()})
        self._write(self.requests_dir / f"{request_id}.json", record)

        body = {
            "kind": "call",
            "id": request_id,
            "tool": record["tool"],
            "subject": record.get("subject"),
            "digest": record["digest"],
            "targets": record.get("targets", []),
            "expires_at": time.time() + (ttl_seconds or DEFAULT_CALL_TTL_SECONDS),
            "max_uses": 1,
            "used": 0,
        }
        return self._issue(body)

    @_locked
    def deny(self, request_id: str) -> dict[str, Any]:
        record = self.request_record(request_id)
        if record.get("status") != "pending":
            raise ApprovalError(f"请求 {request_id} 当前状态是 {record.get('status')}，不能拒绝。")
        record.update({"status": "denied", "decided_at": time.time()})
        return self._write(self.requests_dir / f"{request_id}.json", record)

    @_locked
    def grant_scope(self, tools: list[str], paths: list[str], ttl_seconds: int | None = None,
                    max_uses: int | None = None) -> dict[str, Any]:
        """批准一组工具在一段时间内的写入范围。"""
        raise ApprovalError("范围授权已停用；请逐次审核并批准具体调用。")

    # ------------------------------------------------------------------ 校验
    @_locked
    def consume(self, tool: str, arguments: Any, *, subject: str | None = None) -> dict[str, Any] | None:
        """找到覆盖本次调用的批准记录；一次性记录在校验通过后立即消耗。"""
        digest = canonical_digest(tool, arguments)
        call_hit = None
        for grant in self._grants():
            if (grant["kind"] != "call" or grant["tool"] != tool
                    or grant["digest"] != digest or grant.get("subject") != subject):
                continue
            call_hit = grant
            break
        if call_hit is not None:
            self._consume(call_hit)
            return call_hit

        # 旧范围授权也不能生效：工具可能修改隐式活动文档或引用外部文件。
        return None

    def _scope_covers(self, grant: dict[str, Any], tool: str, tool_targets: list[str]) -> bool:
        allowed = grant.get("tools") or []
        if "*" not in allowed and tool not in allowed:
            return False
        if tool not in allowed and "*" in allowed:
            # * 表示"全部写工具"，但破坏性操作必须逐个点名，避免一把梭。
            if classify(tool)[0] is Effect.DESTRUCTIVE:
                return False
        try:
            prefixes = [_normalize_path(item) for item in grant.get("paths") or []]
        except (OSError, ValueError):
            return False
        if not prefixes:
            return True
        if not tool_targets:
            return False
        try:
            normalized_targets = [_normalize_path(target) for target in tool_targets]
        except (OSError, ValueError):
            return False
        return all(
            any(_within(target, prefix) for prefix in prefixes)
            for target in normalized_targets
        )

    def _grants(self) -> list[dict[str, Any]]:
        now = time.time()
        valid = []
        for path in sorted(self.grants_dir.glob("*.json")):
            grant = self._read(path)
            if grant.get("expires_at", 0) <= now:
                continue
            if grant.get("max_uses") is not None and grant.get("used", 0) >= grant["max_uses"]:
                continue
            if not hmac.compare_digest(self._sign(_unsigned(grant)), grant.get("signature", "")):
                continue
            valid.append(grant)
        return valid

    def _consume(self, grant: dict[str, Any]) -> None:
        grant["used"] = grant.get("used", 0) + 1
        grant["used_at"] = time.time()
        self._write(self.grants_dir / f"{grant['id']}.json", self._sign_record(grant))

    # ------------------------------------------------------------------ 存储
    def _issue(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self._sign_record(body)
        self._write(self.grants_dir / f"{body['id']}.json", record)
        return record

    def _sign_record(self, body: dict[str, Any]) -> dict[str, Any]:
        record = dict(body)
        record["signature"] = self._sign(_unsigned(record))
        return record

    @staticmethod
    def _write(path: Path, record: dict[str, Any]) -> dict[str, Any]:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return record

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ApprovalError(f"没有找到批准记录：{path.name}") from exc
        except json.JSONDecodeError as exc:
            raise ApprovalError(f"批准记录已损坏：{path.name}") from exc


def _unsigned(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "signature"}


def _normalize_path(value: Any) -> str:
    path = Path(str(value))
    if not path.is_absolute():
        raise ValueError('审批范围和目标必须是绝对路径')
    return os.path.normcase(str(path.resolve(strict=False)))


def _within(target: str, prefix: str) -> bool:
    """按路径段匹配，避免 D:/work 误覆盖 D:/workshop。"""
    try:
        return os.path.commonpath((target, prefix)) == prefix
    except ValueError:
        return False
