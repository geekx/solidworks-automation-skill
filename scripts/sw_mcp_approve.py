"""人工批准 SolidWorks MCP 的写操作。

用法::

    python scripts/sw_mcp_approve.py list
    python scripts/sw_mcp_approve.py approve 1a2b3c4d
    python scripts/sw_mcp_approve.py deny 1a2b3c4d

MCP 调用参数中的 ``approved=true`` 不会产生批准记录。交互式终端检查只防止
普通管道误操作，不能证明输入来自真人；能以同一操作系统用户运行代码的进程
可以访问本地账本或模拟终端，本机制不能隔离此类进程。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from approval import ApprovalError, ApprovalLedger, default_root  # noqa: E402


def _format_time(timestamp: float | None) -> str:
    if not timestamp:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _show_list(ledger: ApprovalLedger) -> int:
    print(f"批准记录目录: {ledger.root}")
    pending = ledger.pending()
    if pending:
        print("\n待批准:")
        for item in pending:
            remaining = max(0, int(item["expires_at"] - time.time()) // 60)
            print(f"  [{item['id']}] {item['tool']} ({item['effect']}, 剩 {remaining} 分钟)")
            print(f"        参数: {item['summary'][:300]}")
            if item.get("targets"):
                print(f"        目标: {', '.join(item['targets'])}")
    else:
        print("\n待批准: 无")

    decisions = ledger.decisions(5)
    if decisions:
        print("\n最近决定:")
        for item in decisions:
            print(f"  [{item['id']}] {item['status']} {item['tool']} @ {_format_time(item.get('decided_at'))}")
    return 0


def _confirm(prompt: str) -> bool:
    """要求交互式输入；TTY 本身不证明输入来自真人。"""
    if not sys.stdin.isatty():
        print("拒绝：批准必须在交互式终端里由人完成（当前标准输入不是终端）。", file=sys.stderr)
        return False
    try:
        answer = input(f"{prompt} 输入 yes 确认: ")
    except (EOFError, KeyboardInterrupt):
        # Windows 的 NUL 设备也会让 isatty() 返回真，必须兜住读不到输入的情况。
        print("\n拒绝：没有收到人工确认。", file=sys.stderr)
        return False
    return answer.strip().lower() == "yes"


def _show_request(ledger: ApprovalLedger, request_id: str) -> dict:
    record = ledger.request_record(request_id)
    print(f"工具: {record['tool']}  等级: {record['effect']}  请求: {request_id}")
    print(f"参数: {record['summary']}")
    if record.get("targets"):
        print(f"目标: {', '.join(record['targets'])}")
    return record


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="人工批准 SolidWorks MCP 写操作")
    parser.add_argument("--dir", default=None, help="批准记录目录（默认取 SW_MCP_APPROVAL_DIR）")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出待批准请求和最近决定")

    approve = sub.add_parser("approve", help="批准一条待处理请求")
    approve.add_argument("request_id")
    approve.add_argument("--ttl", type=int, default=None, help="批准有效期（秒）")

    deny = sub.add_parser("deny", help="拒绝一条待处理请求")
    deny.add_argument("request_id")

    scope = sub.add_parser("scope", help="范围授权已停用（仅保留命令兼容）")
    scope.add_argument("--tool", action="append", required=True, help="工具名，可重复；* 表示全部写工具")
    scope.add_argument("--path", action="append", default=[], help="允许的目录前缀，可重复；省略表示不限目录")
    scope.add_argument("--ttl", type=int, default=None, help="有效期（秒）")
    scope.add_argument("--uses", type=int, default=None, help="最多使用次数；省略表示不限")

    args = parser.parse_args(argv)
    ledger = ApprovalLedger(args.dir or default_root())
    command = args.command or "list"

    try:
        if command == "list":
            return _show_list(ledger)
        if command == "approve":
            record = _show_request(ledger, args.request_id)
            if not _confirm(f"批准 {record['tool']} 这次调用？"):
                return 1
            grant = ledger.approve(args.request_id, args.ttl, expected_record=record)
            print(f"已批准，有效期至 {_format_time(grant['expires_at'])}，仅本次调用可用。")
            return 0
        if command == "deny":
            _show_request(ledger, args.request_id)
            if not _confirm("拒绝这次调用？"):
                return 1
            ledger.deny(args.request_id)
            print("已拒绝。")
            return 0
        if command == "scope":
            raise ApprovalError("范围授权已停用；请对具体调用逐次审批。")
    except ApprovalError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    parser.error(f"未知命令: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
