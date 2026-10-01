from dashboard.observability import PipelineProgress


def test_pipeline_progress_emits_stage_diagnostic_and_finish():
    messages = []
    progress = PipelineProgress(total=1, label="TEST", emit=messages.append)

    assert progress.run("load data", lambda: 7) == 7
    progress.diagnostic("dataset", rows=3, latest="2026-09-30")
    progress.progress("backfill", 10, 20)
    progress.finish("SUCCESS")

    joined = "\n".join(messages)
    assert "[01/01] START load data" in joined
    assert "[01/01] DONE  load data | elapsed=" in joined
    assert "[DIAGNOSTIC] dataset | rows=3 | latest=2026-09-30" in joined
    assert "[PROGRESS] backfill | 10/20" in joined
    assert "[TEST] SUCCESS | total_elapsed=" in joined


def test_pipeline_progress_logs_failure_without_swallowing_exception():
    messages = []
    progress = PipelineProgress(total=1, emit=messages.append)

    try:
        progress.run("parse", lambda: (_ for _ in ()).throw(ValueError("bad")))
    except ValueError as exc:
        assert str(exc) == "bad"
    else:
        raise AssertionError("ValueError was not reraised")

    assert "[01/01] FAIL  parse | elapsed=" in messages[-1]
    assert "error=ValueError" in messages[-1]


def test_diagnostic_format_failure_never_breaks_business_pipeline():
    class BadValue:
        def __str__(self):
            raise RuntimeError("diagnostic formatting failed")

    messages = []
    progress = PipelineProgress(total=0, emit=messages.append)

    progress.diagnostic("unsafe_value", value=BadValue())

    assert messages == [
        "[DIAGNOSTIC] unsafe_value | diagnostic_status=FORMAT_FAILED | error=RuntimeError"
    ]


def test_diagnostic_emit_failure_is_best_effort():
    def broken_emit(_message):
        raise RuntimeError("log sink unavailable")

    progress = PipelineProgress(total=0, emit=broken_emit)

    # Observability must not become a new production failure mode.
    progress.diagnostic("dataset", rows=3)
