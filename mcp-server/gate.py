"""发现模式与原生直调共用的唯一网关。

FastMCP 的 ``list_tools`` / ``call_tool`` 都在调用时读取 ``self._tool_manager``，
所以把注册表换成这个代理，就同时覆盖 stdio 和 streamable-http，也覆盖"客户端
直接点名调用被隐藏的原工具"这条绕过路径。
"""
from __future__ import annotations

import logging
import json
import secrets
import threading
import weakref
from contextlib import nullcontext
from typing import Any, Sequence

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ClientCapabilities, ElicitationCapability, Tool as MCPTool
from pydantic import BaseModel

import policy
from approval import ApprovalError, ApprovalLedger
from discovery import ToolCatalog
from document_identity import DocumentIdentity, DocumentIdentityError, bind_document

logger = logging.getLogger("solidworks_mcp.gate")

DISCOVERY_TOOLS = ("sw_search_tools", "sw_describe_tool", "sw_call_tool")

MODES = ("full", "search")
APPROVAL_MODES = ("auto", "task", "server", "client", "elicit", "off")
_EXTERNAL_APPROVAL_MODES = ("task", "client", "off")


class _Confirm(BaseModel):
    approve: bool


class ToolGate:
    """注册表代理：控制可见性，并在执行前统一做策略与审批。"""

    def __init__(self, manager: Any, *, mode: str = "search", approval_mode: str = "auto",
                 ledger: ApprovalLedger | None = None):
        if mode not in MODES:
            raise ValueError(f"SW_MCP_DISCOVERY 只能是 {'/'.join(MODES)}，收到 {mode!r}")
        if approval_mode not in APPROVAL_MODES:
            raise ValueError(f"SW_MCP_APPROVAL 只能是 {'/'.join(APPROVAL_MODES)}，收到 {approval_mode!r}")
        if mode == "search" and approval_mode == "client":
            raise ValueError("发现模式不能使用 client 审批：客户端只能看到通用入口，无法按原工具审批。")
        self._manager = manager
        self.mode = mode
        self.approval_mode = "task" if (approval_mode == "auto" and mode == "search") else (
            "client" if approval_mode == "auto" else approval_mode)
        # 注册工具、打包与只读调用不应在用户目录中创建审批账本。
        self._ledger = ledger
        self._catalog: ToolCatalog | None = None
        self._catalog_size = -1
        self._internal_subject = secrets.token_hex(16)
        self._subjects: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
        self._subjects_lock = threading.Lock()

    @property
    def ledger(self) -> ApprovalLedger:
        if self._ledger is None:
            self._ledger = ApprovalLedger()
        return self._ledger

    def _subject(self, context: Any) -> str:
        """绑定服务端会话，而不是信任模型传入的客户端标识。"""
        if context is None:
            return self._internal_subject
        try:
            session = context.session
            with self._subjects_lock:
                subject = self._subjects.get(session)
                if subject is None:
                    subject = secrets.token_hex(16)
                    self._subjects[session] = subject
                return subject
        except (AttributeError, TypeError, ValueError) as exc:
            raise ToolError("无法绑定审批到当前客户端会话，拒绝执行。") from exc

    # ------------------------------------------------------------ 注册表代理
    @property
    def _tools(self) -> dict[str, Any]:
        """打包脚本需要读取完整注册表，发现模式不能影响发布产物。"""
        return self._manager._tools

    def add_tool(self, *args: Any, **kwargs: Any) -> Any:
        tool = self._manager.add_tool(*args, **kwargs)
        self._catalog = None
        return tool

    def remove_tool(self, name: str) -> Any:
        self._catalog = None
        return self._manager.remove_tool(name)

    def get_tool(self, name: str) -> Any:
        return self._manager.get_tool(name)

    def list_tools(self) -> list[Any]:
        tools = self._manager.list_tools()
        if self.mode == "full":
            return [tool for tool in tools if tool.name not in DISCOVERY_TOOLS]
        return [tool for tool in tools if tool.name in DISCOVERY_TOOLS]

    # ------------------------------------------------------------ 检索
    def catalog(self) -> ToolCatalog:
        tools = [tool for tool in self._manager.list_tools() if tool.name not in DISCOVERY_TOOLS]
        if self._catalog is None or len(tools) != self._catalog_size:
            self._catalog = ToolCatalog(
                [MCPTool(name=tool.name, title=tool.title, description=tool.description,
                         inputSchema=tool.parameters, outputSchema=tool.output_schema,
                         annotations=tool.annotations, icons=tool.icons,
                         _meta=tool.meta).model_dump(by_alias=True, exclude_none=True)
                 for tool in tools],
                read_names={tool.name for tool in tools
                            if policy.classify(tool.name)[0] is policy.Effect.READ},
            )
            self._catalog_size = len(tools)
        return self._catalog

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        results = self.catalog().search(query, limit)
        for item in results:
            effect, _ = policy.classify(item["name"])
            item["effect"] = effect.value
            blocked = self._blocked_reason(item["name"])
            item["available"] = blocked is None
            if (self.approval_mode not in _EXTERNAL_APPROVAL_MODES
                    and policy.supports_document_identity(item["name"])):
                item["document_target_required"] = True
            if blocked:
                item["blocked_reason"] = blocked
        return results

    def describe(self, name: str) -> dict[str, Any] | None:
        tool = self.catalog().describe(name)
        if tool is None:
            return None
        view = {key: tool[key] for key in ("name", "title", "description", "inputSchema", "outputSchema", "annotations", "icons", "_meta") if key in tool}
        effect, note = policy.classify(name)
        view["effect"] = effect.value
        view["approval_required"] = (policy.requires_approval(effect)
                                     and self.approval_mode not in _EXTERNAL_APPROVAL_MODES)
        view["approval_mode"] = self.approval_mode
        if (self.approval_mode not in _EXTERNAL_APPROVAL_MODES
                and policy.supports_document_identity(name)):
            view["document_target_required"] = True
            view["document_target_note"] = "通过 sw_call_tool 提供已保存文档的绝对路径和 SolidWorks 进程 ID。"
        if note:
            view["policy_note"] = note
        blocked = self._blocked_reason(name)
        view["available"] = blocked is None
        if blocked:
            view["blocked_reason"] = blocked
        return view

    def _blocked_reason(self, name: str) -> str | None:
        if (self.approval_mode not in _EXTERNAL_APPROVAL_MODES
                and policy.needs_active_document_identity(name)
                and not policy.supports_document_identity(name)):
            return (
                f"已阻止：{name} 依赖 SolidWorks 活动文档；当前服务端审批无法绑定并复核文档身份。"
                "请使用具有可信逐工具审批的原生模式，或等待文档身份绑定功能。"
            )
        return None

    # ------------------------------------------------------------ 执行
    async def call_tool(self, name: str, arguments: dict[str, Any] | None,
                        context: Any = None, convert_result: bool = False,
                        document_target: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        if name in DISCOVERY_TOOLS:
            return await self._manager.call_tool(name, arguments, context=context, convert_result=convert_result)

        effect, note = policy.classify(name, arguments)
        if effect is policy.Effect.UNKNOWN:
            raise ToolError(
                f"工具 {name} 未登记副作用等级，服务端拒绝执行。"
                "请先在 mcp-server/policy.py 补充分级后再发布。"
            )

        blocked = self._blocked_reason(name)
        if blocked:
            raise ToolError(blocked)

        identity = None
        approval_arguments: dict[str, Any] = arguments
        if document_target is not None and policy.needs_active_document_identity(name) and not policy.supports_document_identity(name):
            raise ToolError(f"{name} 暂不支持 document_target 校验，无法使用此目标参数。")
        if (policy.supports_document_identity(name)
                and (self.approval_mode not in _EXTERNAL_APPROVAL_MODES or document_target is not None)):
            if not isinstance(document_target, dict) or set(document_target) != {"process_id", "path"}:
                raise ToolError("该操作需要 document_target：已保存文档绝对路径 path 和 SolidWorks 进程 ID process_id。")
            try:
                identity = DocumentIdentity.create(document_target["process_id"], document_target["path"])
            except DocumentIdentityError as exc:
                raise ToolError(str(exc)) from exc
            approval_arguments = {"arguments": arguments, "document_target": {
                "process_id": identity.process_id, "path": identity.path,
            }}

        if policy.requires_approval(effect) and self.approval_mode not in _EXTERNAL_APPROVAL_MODES:
            subject = self._subject(context)
            allowed = await self._authorize(name, approval_arguments, effect, note, context, subject)
            if not allowed:
                raise ToolError(allowed_reason(self.ledger, name, approval_arguments, effect, subject))

        with bind_document(identity) if identity else nullcontext():
            return await self._manager.call_tool(name, arguments, context=context, convert_result=convert_result)

    async def _authorize(self, name: str, arguments: dict[str, Any], effect: policy.Effect,
                         note: str, context: Any, subject: str) -> bool:
        if self.ledger.consume(name, arguments, subject=subject) is not None:
            return True
        if self.approval_mode == "elicit":
            return await self._confirm_via_client(name, arguments, effect, note, context)
        return False

    async def _confirm_via_client(self, name: str, arguments: dict[str, Any], effect: policy.Effect,
                                  note: str, context: Any) -> bool:
        """走 MCP 用户交互向人确认；客户端不支持时按未批准处理。"""
        if context is None:
            return False
        try:
            capabilities = context.session.check_client_capability(
                ClientCapabilities(elicitation=ElicitationCapability(form=None)))
        except Exception:
            return False
        if not capabilities:
            return False
        # 不能让用户在看不到完整参数时批准操作。超出交互消息的安全上限时，
        # 转入带外审批，让审批端展示完整参数，而不是静默截断。
        parameters = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        if len(parameters) > 4000:
            logger.warning("审批参数过长，不能通过 elicitation 完整展示")
            return False
        targets = policy.targets(arguments)
        message = (
            f"SolidWorks MCP 请求执行写操作。\n工具: {name}\n等级: {effect.value}\n"
            f"参数: {parameters}"
            + (f"\n目标: {', '.join(targets)}" if targets else "")
            + (f"\n注意: {note}" if note else "")
        )
        try:
            result = await context.elicit(message=message, schema=_Confirm)
        except Exception as exc:  # 客户端可能在运行中拒绝交互
            logger.warning("elicitation 失败: %s", exc)
            return False
        return result.action == "accept" and bool(result.data.approve)


def allowed_reason(ledger: ApprovalLedger, name: str, arguments: dict[str, Any],
                   effect: policy.Effect, subject: str | None = None) -> str:
    """未批准时返回给模型的阻塞说明，包含可执行的人工批准步骤。"""
    try:
        request = ledger.request(name, arguments, effect.value, subject=subject)
        request_id = request["id"]
        detail = f"审批请求 {request_id}（{int(request['expires_at'] - request['created_at'])} 秒内有效）"
    except ApprovalError as exc:  # 账本不可写时不能假装只是"等待批准"
        return f"工具 {name} 需要人工批准，但审批账本不可用：{exc}"
    return (
        f"已阻止：{name} 属于 {effect.value} 操作，需要人工批准后才会执行。\n"
        f"{detail}\n"
        f"请在 MCP 服务端所在机器的安装目录打开终端，运行：python scripts/sw_mcp_approve.py approve {request_id}\n"
        f"然后用完全相同的参数重试；参数一旦变化，原批准不再适用。"
    )
