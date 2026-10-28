"""Structured tool errors shared by every Agent capability."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolError:
    """A failed invocation; this is never evidence that a rule does not exist."""

    code: str
    detail: str
    retryable: bool = False
