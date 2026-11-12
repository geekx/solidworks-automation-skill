from __future__ import annotations

import json


def test_bridge_roundtrip_writes_context_and_bumps_revision(bridge):
    context = {"schemaVersion": "1.0", "document": {"documentToken": "doc-" + "a" * 24}}
    bridge.write_context(context)
    assert bridge.read_context()["schemaVersion"] == "1.0"
    assert bridge.read_state()["bridgeRevision"] >= 1


def test_missing_json_returns_default(bridge):
    assert bridge.read_json("nope.json", default=None) is None


def test_corrupted_json_raises_bridge_error(bridge):
    (bridge.bridge_dir() / "context.json").write_text("{not json", encoding="utf-8")
    try:
        bridge.read_context()
    except bridge.BridgeError as exc:
        assert exc.code == "E_BRIDGE_UNREADABLE"
    else:
        raise AssertionError("expected BridgeError for corrupted JSON")


def test_pin_state_and_history(bridge):
    bridge.set_pinned("doc-" + "b" * 24, "sel-" + "c" * 24, ["ent-" + "d" * 24])
    assert bridge.read_state()["pinned"]["entityTokens"] == ["ent-" + "d" * 24]

    bridge.append_history("pin", mode="pinned")
    bridge.append_history("unpin", reason="document_changed")
    history = bridge.read_history(limit=10)
    assert [item["kind"] for item in history] == ["pin", "unpin"]
    assert history[-1]["reason"] == "document_changed"

    bridge.set_pinned(None, None)
    assert bridge.read_state()["pinned"] is None


def test_jsonl_skips_truncated_lines(bridge):
    (bridge.bridge_dir() / "history.jsonl").write_text('{"kind": "pin"}\n{"kind": "unp', encoding="utf-8")
    assert [item["kind"] for item in bridge.read_history()] == ["pin"]


def test_plan_preview_and_journal(bridge):
    bridge.write_plan_preview("plan-1", {"status": "ready", "plan": {"planId": "plan-1"}})
    assert bridge.read_plan_preview("plan-1")["status"] == "ready"
    assert bridge.list_plan_previews()[0]["plan"]["planId"] == "plan-1"

    bridge.append_journal({"entryId": "op-1", "event": "commit", "planId": "plan-1"})
    assert bridge.read_journal()[-1]["event"] == "commit"


def test_document_revision_counter(bridge):
    token = "doc-" + "e" * 24
    assert bridge.document_revision(token) == 0
    assert bridge.bump_document_revision(token) == 1
    assert bridge.bump_document_revision(token) == 2
    assert bridge.document_revision(token) == 2


def test_prompt_request_and_highlight(bridge):
    record = bridge.append_prompt_request("把这个孔改成 Ø8", context={"document": {"documentToken": "doc-" + "f" * 24}})
    assert record["status"] == "pending"
    assert bridge.read_prompt_requests()[-1]["prompt"].startswith("把这个孔")

    path = bridge.write_highlight_request(["ent-" + "a" * 24], ttl_seconds=5)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entityTokens"] == ["ent-" + "a" * 24]
    assert payload["expiresAt"] > payload["requestedAt"]
