from __future__ import annotations

import sw_parallel_pipeline as pipeline


def _card(**overrides):
    card = {
        "task_id": "NA-PushStop-travel-80",
        "modeling": {
            "micro_ops": ["modify_sketch_dimension", "force_rebuild"],
            "gate_ops": ["modify_sketch_dimension"],
            "invariants": ["preserve_hole_center"],
        },
        "evaluation": {"checks": ["travel_range_check", "interference_all_pairs"]},
        "coordination": {"max_parallel_writers": 1, "evaluation_deadline_ms": 8000},
    }
    card.update(overrides)
    return card


def _coordinator(executor=None, evaluators=(), on_rollback=None):
    stream = pipeline.RevisionStream()
    queue = pipeline.MicroOpQueue(stream, executor or (lambda op: {"status": "applied", "op": op.kind}))
    return pipeline.Coordinator(stream, queue, evaluators, on_rollback=on_rollback), stream


def test_revision_stream_is_monotonic():
    stream = pipeline.RevisionStream()
    assert stream.publish({"kind": "a"}) == 1
    assert stream.publish({"kind": "b"}) == 2
    assert len(stream.since(0)) == 2
    assert stream.since(1)[0]["kind"] == "b"


def test_micro_op_queue_runs_in_declared_order():
    seen = []
    coordinator, stream = _coordinator(executor=lambda op: (seen.append(op.kind), {"status": "applied"})[1])
    result = coordinator.run(_card())
    assert seen == ["modify_sketch_dimension", "force_rebuild"]
    assert result["status"] == "completed"
    assert result["revision"] == 2


def test_gate_failure_triggers_rollback_and_stops():
    calls = []

    def executor(op):
        calls.append(op.kind)
        return {"status": "applied"}

    def evaluator(payload):
        return {"id": "travel_range_check", "result": "FAIL", "detail": "measured 78.2 < 80"}

    rollbacks = []
    coordinator, _ = _coordinator(
        executor=executor,
        evaluators=[evaluator],
        on_rollback=lambda op, report: (rollbacks.append(op.op_id), {"status": "rolled_back"})[1],
    )
    result = coordinator.run(_card())
    assert result["status"] == "rolled_back"
    assert result["stoppedAt"] == "NA-PushStop-travel-80-op1"
    assert len(rollbacks) == 1
    assert calls == ["modify_sketch_dimension"]


def test_non_gate_warning_does_not_stop_pipeline():
    def evaluator(payload):
        return {"id": "rail_load_check", "result": "WARN", "detail": "余量 8%"}

    coordinator, _ = _coordinator(evaluators=[evaluator])
    result = coordinator.run(_card())
    assert result["status"] == "warn"
    assert len(result["ops"]) == 2


def test_executor_failure_marks_pipeline_failed():
    def executor(op):
        if op.kind == "force_rebuild":
            raise RuntimeError("rebuild exploded")
        return {"status": "applied"}

    coordinator, _ = _coordinator(executor=executor)
    result = coordinator.run(_card())
    assert result["status"] == "failed"
    assert "rebuild exploded" in result["ops"][-1]["error"]


def test_task_card_requires_single_writer_and_checks():
    coordinator, _ = _coordinator()
    bad = _card(coordination={"max_parallel_writers": 2})
    result = coordinator.run(bad)
    assert result["status"] == "invalid"
    assert any("max_parallel_writers" in error for error in result["errors"])


def test_task_card_missing_evaluation_is_invalid():
    coordinator, _ = _coordinator()
    card = _card()
    card.pop("evaluation")
    assert coordinator.run(card)["status"] == "invalid"


def test_evaluation_report_aggregates_worst_result():
    report = pipeline.evaluation_report(7, [{"id": "a", "result": "PASS"}, {"id": "b", "result": "FAIL"}])
    assert report["overall"] == "FAIL"
    assert report["action"] == "rollback_and_replan"
    assert pipeline.gate_decision(report, gate=True) == "rollback"
    assert pipeline.gate_decision(report, gate=False) == "warn"
