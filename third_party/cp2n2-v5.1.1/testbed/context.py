"""Request-scoped trace propagation shared by HTTP control components."""

from __future__ import annotations

from contextvars import ContextVar, Token


TRACE_ID_HEADER = "X-CP2N2-Trace-ID"
PARENT_SPAN_HEADER = "X-CP2N2-Parent-Span-ID"

current_trace_id: ContextVar[str | None] = ContextVar(
    "cp2n2_trace_id", default=None
)
current_parent_span_id: ContextVar[str | None] = ContextVar(
    "cp2n2_parent_span_id", default=None
)


def bind_trace(trace_id: str, parent_span_id: str) -> tuple[Token, Token]:
    return (
        current_trace_id.set(trace_id),
        current_parent_span_id.set(parent_span_id),
    )


def reset_trace(tokens: tuple[Token, Token]) -> None:
    current_trace_id.reset(tokens[0])
    current_parent_span_id.reset(tokens[1])


def propagation_headers() -> dict[str, str]:
    trace_id = current_trace_id.get()
    parent_span_id = current_parent_span_id.get()
    headers: dict[str, str] = {}
    if trace_id:
        headers[TRACE_ID_HEADER] = trace_id
    if parent_span_id:
        headers[PARENT_SPAN_HEADER] = parent_span_id
    return headers
