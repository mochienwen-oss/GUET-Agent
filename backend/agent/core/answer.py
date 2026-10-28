"""Authoritative delivery object shared by graph, HTTP and persistence."""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class FinalAnswer:
    status: str
    text: str
    contexts: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    model_suggestions: list = field(default_factory=list)
    audit: dict = field(default_factory=dict)
    version: str = "final-answer/1"

    def __post_init__(self):
        if self.status not in {"answer", "partial", "needs_input", "missing_policy", "no_evidence", "failed"}:
            raise ValueError("未知答案状态")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("最终答案正文不能为空")

    def to_dict(self):
        return asdict(self)
