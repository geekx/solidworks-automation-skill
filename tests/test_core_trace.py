"""@brief Execution Trace adapter 单元测试（复用 queue/events JSONL，不依赖 SolidWorks）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.trace import (  # noqa: E402
    TRACE_EVENT_TYPES,
    append_event,
    emit,
    read_events,
    redact,
    sanitize_id,
    trace_path_for,
)


def test_trace_event_types_include_canonical_vocabulary():
    for name in (
        "run.created",
        "run.completed",
        "tool.failed",
        "verification.passed",
        "recovery.retry",
        "capability.gap",
    ):
        assert name in TRACE_EVENT_TYPES


def test_emit_and_read(tmp_path):
    path = emit(tmp_path, "run.created", run_id="run-1", message="created")
    assert path == trace_path_for(tmp_path, "run-1")
    events = read_events(tmp_path, "run-1")
    assert len(events) == 1
    assert events[0]["type"] == "run.created"
    assert events[0]["run_id"] == "run-1"
    assert events[0]["message"] == "created"


def test_multiple_events_preserve_order(tmp_path):
    emit(tmp_path, "run.started", run_id="r1", message="start")
    emit(tmp_path, "step.started", run_id="r1", step_id="s1", parent_step_id=None, message="step")
    emit(tmp_path, "tool.succeeded", run_id="r1", step_id="s1", message="tool ok")
    events = read_events(tmp_path, "r1")
    assert [event["type"] for event in events] == ["run.started", "step.started", "tool.succeeded"]
    assert events[1]["step_id"] == "s1"
    assert events[2]["step_id"] == "s1"


def test_legacy_schema_events_are_readable(tmp_path):
    # 旧 queue_worker 事件（camelCase、job 维度、无 step_id）与新 trace 事件同文件共存
    legacy = {
        "type": "run.claimed",
        "jobId": "job-1",
        "runId": "run-1",
        "status": "running",
        "progress": 12,
        "message": "claimed",
        "at": "2026-01-01T00:00:00+00:00",
        "worker": "cad-workbench-python-worker",
    }
    append_event(tmp_path, legacy, key="run-1")
    emit(tmp_path, "step.started", run_id="run-1", step_id="s1", message="step")

    events = read_events(tmp_path, "run-1")
    assert len(events) == 2
    assert events[0]["type"] == "run.claimed"
    assert events[0]["jobId"] == "job-1"
    assert "step_id" not in events[0]
    assert events[1]["type"] == "step.started"
    assert events[1]["step_id"] == "s1"


def test_trace_redacts_sensitive_fields(tmp_path):
    path = emit(
        tmp_path,
        "tool.called",
        run_id="r1",
        step_id="s1",
        data={"prompt": "secret prompt", "api_key": "sk-123"},
    )
    raw = path.read_text(encoding="utf-8")
    assert "secret prompt" not in raw
    assert "sk-123" not in raw
    event = read_events(tmp_path, "r1")[0]
    assert event["data"]["prompt"] == "[redacted]"
    assert event["data"]["api_key"] == "[redacted]"


def test_redact_helper():
    assert redact(
        {"api_key": "x", "nested": {"token": "y"}, "ok": "z"}
    ) == {"api_key": "[redacted]", "nested": {"token": "[redacted]"}, "ok": "z"}
    assert redact([{"password": "p"}]) == [{"password": "[redacted]"}]
    assert redact("plain") == "plain"


def test_append_only_does_not_truncate(tmp_path):
    emit(tmp_path, "run.started", run_id="r1", message="one")
    emit(tmp_path, "run.completed", run_id="r1", message="two")
    events = read_events(tmp_path, "r1")
    assert len(events) == 2
    assert events[0]["message"] == "one"
    assert events[1]["message"] == "two"


def test_sanitize_id():
    assert sanitize_id("a/b\\c d") == "abcd"
    assert sanitize_id("") == "unknown"
    assert sanitize_id(None) == "unknown"


def test_read_missing_returns_empty(tmp_path):
    assert read_events(tmp_path, "nope") == []


def test_read_skips_corrupt_lines(tmp_path):
    path = trace_path_for(tmp_path, "r1")
    emit(tmp_path, "run.created", run_id="r1", message="ok")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
    emit(tmp_path, "run.completed", run_id="r1", message="done")
    events = read_events(tmp_path, "r1")
    assert [event["type"] for event in events] == ["run.created", "run.completed"]
