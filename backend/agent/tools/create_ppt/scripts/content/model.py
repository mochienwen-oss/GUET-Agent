"""与 PPTX 排版解耦的内容模型。备注中的事实同样用 Claim 表达。"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    gap: str | None = None
    column: str | None = None  # 对比页：left / right
    kind: str = "fact"  # fact / suggestion / analogy；非事实表达不得冒充学校规定


@dataclass(frozen=True)
class Slide:
    kind: str
    title: str
    claims: list[Claim] = field(default_factory=list)
    notes: list[Claim] = field(default_factory=list)
    items: list[str] = field(default_factory=list)  # 目录等非事实导航文字
    subtitle: str = ""
    left_title: str = ""
    right_title: str = ""
    slide_id: str = ""
    purpose: str = ""
    chart: dict | None = None
    table: dict | None = None


def slide_dict(slide: Slide) -> dict:
    from dataclasses import asdict
    return asdict(slide)


def read_slide(data: dict) -> Slide:
    fields = {key: value for key, value in data.items() if key in Slide.__dataclass_fields__}
    for key in ("claims", "notes"):
        fields[key] = [Claim(**item) for item in fields.get(key) or []]
    return Slide(**fields)


@dataclass(frozen=True)
class Outline:
    slides: list[Slide]


@dataclass(frozen=True)
class QaIssue:
    code: str
    slide: int
    detail: str


@dataclass(frozen=True)
class QaReport:
    issues: list[QaIssue]

    @property
    def ok(self) -> bool:
        return not self.issues
