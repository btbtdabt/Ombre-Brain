from operation_runtime import run_optional_operation
from ombrebrain.app.execution import ExecutionEnvelope


class _Runtime:
    def __init__(self) -> None:
        self.envelope: ExecutionEnvelope | None = None

    def run_operation(self, envelope, handler):
        self.envelope = envelope
        return f"wrapped:{handler()}"


def test_optional_operation_falls_back_without_runtime() -> None:
    assert run_optional_operation(
        None,
        "read",
        None,
        lambda: "legacy",
        module="tests",
        actor_name="tester",
        source="tests",
    ) == "legacy"


def test_optional_operation_builds_one_shared_envelope() -> None:
    runtime = _Runtime()

    result = run_optional_operation(
        runtime,
        "write",
        {"bucket_id": "abc"},
        lambda: "result",
        module="tools.trace",
        permissions=("memory:write",),
        actor_name="tool",
        source="mcp",
        writes_memory=True,
    )

    assert result == "wrapped:result"
    assert runtime.envelope is not None
    assert runtime.envelope.operation == "write"
    assert runtime.envelope.payload == {"bucket_id": "abc"}
    assert runtime.envelope.writes_memory is True
