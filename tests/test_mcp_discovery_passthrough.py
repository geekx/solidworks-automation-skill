"""通过真实 stdio 协议比较原生工具和发现网关的结果语义。"""
from __future__ import annotations

import asyncio
import base64
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def _run_fixture_server() -> None:
    """只替换测试进程中的只读工具，避免触及真实 SolidWorks。"""
    sys.path.insert(0, str(ROOT / "mcp-server"))
    import server
    from mcp.types import ImageContent, TextContent

    def structured() -> dict[str, int]:
        """返回结构化测试结果。"""
        return {"value": 42}

    def image():
        """返回图片及文字测试结果。"""
        return [TextContent(type="text", text="preview"), ImageContent(
            type="image", data=base64.b64encode(b"test-png").decode("ascii"), mimeType="image/png"
        )]

    def failure() -> str:
        """返回测试异常。"""
        raise ValueError("fixture failure")

    for name in ("solidworks_health_check", "solidworks_mass_properties", "solidworks_interference_check"):
        server._gate.remove_tool(name)
    server.mcp.add_tool(structured, name="solidworks_health_check")
    server.mcp.add_tool(image, name="solidworks_mass_properties", structured_output=False)
    server.mcp.add_tool(failure, name="solidworks_interference_check")
    if "--http" in sys.argv:
        server.mcp.settings.port = int(os.environ["TEST_MCP_PORT"])
        server.mcp.run(transport="streamable-http")
    else:
        server.mcp.run(transport="stdio")


async def _results(mode: str):
    env = {**os.environ, "SW_MCP_DISCOVERY": mode, "SW_MCP_APPROVAL": "auto"}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "--fixture"],
                                   cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            results = []
            for name in ("solidworks_health_check", "solidworks_mass_properties", "solidworks_interference_check"):
                if mode == "full":
                    results.append(await session.call_tool(name, {}))
                else:
                    results.append(await session.call_tool("sw_call_tool", {"name": name, "arguments": {}}))
            return results


def _assert_results(native, gateway):
    assert native[0].structuredContent == gateway[0].structuredContent == {"value": 42}
    assert [item.model_dump() for item in native[0].content] == [item.model_dump() for item in gateway[0].content]
    assert [item.model_dump() for item in native[1].content] == [item.model_dump() for item in gateway[1].content]
    assert native[1].content[1].type == "image"
    assert native[2].isError and gateway[2].isError
    assert "fixture failure" in native[2].content[0].text
    assert "fixture failure" in gateway[2].content[0].text


def test_stdio_gateway_preserves_structured_images_and_errors():
    _assert_results(asyncio.run(_results("full")), asyncio.run(_results("search")))


def _http_results(mode, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "SW_MCP_DISCOVERY": mode, "SW_MCP_APPROVAL": "auto", "TEST_MCP_PORT": str(port)}
    url = f"http://127.0.0.1:{port}/mcp"
    with (tmp_path / f"{mode}.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--fixture", "--http"],
                                cwd=str(ROOT), env=env, stdout=log, stderr=log)
        try:
            for _ in range(150):
                if proc.poll() is not None:
                    raise AssertionError("HTTP fixture server exited early")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("HTTP fixture server did not start")

            async def run():
                async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                    async with streamable_http_client(url, http_client=client) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            results = []
                            for name in ("solidworks_health_check", "solidworks_mass_properties", "solidworks_interference_check"):
                                if mode == "full":
                                    results.append(await session.call_tool(name, {}))
                                else:
                                    results.append(await session.call_tool("sw_call_tool", {"name": name, "arguments": {}}))
                            return results
            return asyncio.run(run())
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_http_gateway_preserves_structured_images_and_errors(tmp_path):
    _assert_results(_http_results("full", tmp_path), _http_results("search", tmp_path))


if __name__ == "__main__" and "--fixture" in sys.argv:
    _run_fixture_server()
