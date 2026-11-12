"""匿名 Parallel MCP 适配器：只为公开 SolidWorks API 页面提供受限备用提取。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

_ENDPOINT = "https://mcp.parallel.ai/mcp?tools=web_search,web_fetch"
_MAX_MCP_TEXT = 256 * 1024


class ParallelLookupError(RuntimeError):
    """备用服务不可用或返回的资料无法验证。"""


class ParallelLookupCancelled(Exception):
    """调用方已取消查证。"""


def _official_url(url: Any, version: int, interface: str, member: str) -> bool:
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "help.solidworks.com":
            return False
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            return False
        path = unquote(parsed.path).casefold()
        if parsed.fragment or "\\" in path or any(part in {".", ".."} for part in path.split("/")) or "%" in path:
            return False
        if not path.startswith(f"/{version}/") or "/api/" not in path or interface.casefold() not in path:
            return False
        return interface.casefold().startswith("sw") or member.casefold() in path
    except (TypeError, ValueError):
        return False


def _payload(result: Any) -> dict[str, Any]:
    if result.isError:
        raise ParallelLookupError("Parallel MCP 工具返回错误")
    blocks = [getattr(item, "text", "") for item in result.content if getattr(item, "text", None)]
    if len(blocks) != 1 or len(blocks[0].encode("utf-8")) > _MAX_MCP_TEXT:
        raise ParallelLookupError("Parallel 返回内容缺失或超过限制")
    try:
        data = json.loads(blocks[0])
    except (TypeError, ValueError) as exc:
        raise ParallelLookupError("Parallel 返回内容不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise ParallelLookupError("Parallel 返回结构无效")
    return data


def _verified_extract(payload: dict[str, Any], version: int, interface: str, member: str) -> dict[str, Any] | None:
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    for item in results[:20]:
        if not isinstance(item, dict) or not _official_url(item.get("url"), version, interface, member):
            continue
        title = str(item.get("title") or "")[:200]
        if not re.search(rf"(?<!\d){version}(?!\d)", title) or interface.casefold() not in title.casefold():
            continue
        if not interface.casefold().startswith("sw") and member.casefold() not in title.casefold():
            continue
        excerpts = item.get("excerpts")
        if not isinstance(excerpts, list):
            continue
        text = "\n\n".join(value for value in excerpts[:20] if isinstance(value, str)).strip()
        pos = text.casefold().find(member.casefold())
        if pos < 0:
            continue
        if pos > 5000:
            text = text[max(0, pos - 800):]
        text = text[:6000]
        return {"status": "found", "source": item["url"], "version": version, "title": title,
                "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "provider": "parallel-anonymous",
                "version_evidence": "official_url_and_title", "trust": "untrusted_reference",
                "runtime_verified": False}
    return None


async def _query(version: int, interface: str, member: str, url: str) -> dict[str, Any]:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # 明确使用空认证对象：不读取 API key、不传 Authorization，避免意外计费。
    timeout = httpx.Timeout(connect=8.0, read=18.0, write=8.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout, auth=httpx.Auth(), follow_redirects=False) as client:
        async with streamable_http_client(_ENDPOINT, http_client=client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=18)) as session:
                await session.initialize()
                objective = f"Find the SOLIDWORKS {version} {interface} {member} API signature, parameters and units."
                arguments: dict[str, Any] = {"urls": [url], "objective": objective, "full_content": False}
                first = _payload(await session.call_tool("web_fetch", arguments))
                verified = _verified_extract(first, version, interface, member)
                if verified:
                    return verified
                # 不接受供应商提供的任意网址；只搜索并读取对应版本的官方 API 页面。
                session_id = first.get("session_id")
                search_args: dict[str, Any] = {"objective": objective,
                    "search_queries": [f"site:help.solidworks.com/{version} {interface} {member}"]}
                if isinstance(session_id, str) and len(session_id) <= 1000:
                    search_args["session_id"] = session_id
                search = _payload(await session.call_tool("web_search", search_args))
                candidates = search.get("results") if isinstance(search.get("results"), list) else []
                candidates = [item.get("url") for item in candidates if isinstance(item, dict)
                              and _official_url(item.get("url"), version, interface, member)]
                if not candidates:
                    raise ParallelLookupError("Parallel 未找到匹配版本的官方 API 页面")
                second_args: dict[str, Any] = {"urls": [candidates[0]], "objective": objective, "full_content": False}
                next_session = search.get("session_id") or session_id
                if isinstance(next_session, str) and len(next_session) <= 1000:
                    second_args["session_id"] = next_session
                second = _payload(await session.call_tool("web_fetch", second_args))
                verified = _verified_extract(second, version, interface, member)
                if verified:
                    return verified
                raise ParallelLookupError("Parallel 返回的官方页面未能确认接口和版本")


async def _guard(version: int, interface: str, member: str, url: str,
                 cancelled: Callable[[], bool] | None, timeout: float) -> dict[str, Any]:
    task = asyncio.create_task(_query(version, interface, member, url))
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            if cancelled and cancelled():
                raise ParallelLookupCancelled("用户已取消 API 查证")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ParallelLookupError("Parallel MCP 查证超时")
            await asyncio.wait({task}, timeout=min(0.2, remaining))
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def retrieve_parallel_document(version: int, interface: str, member: str, url: str, *,
                               cancelled: Callable[[], bool] | None = None, timeout: float = 35.0) -> dict[str, Any]:
    """同步入口；只发送版本、接口、方法和官方 URL，不接受凭据或用户任务文本。"""
    if not _official_url(url, version, interface, member):
        raise ParallelLookupError("拒绝非目标版本的官方 API URL")
    try:
        return asyncio.run(_guard(version, interface, member, url, cancelled, timeout))
    except (ParallelLookupError, ParallelLookupCancelled):
        raise
    except Exception as exc:
        raise ParallelLookupError(f"Parallel MCP 不可用: {type(exc).__name__}") from exc
