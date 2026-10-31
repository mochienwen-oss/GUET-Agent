"""首个切片的五种受控版式及文本容量。版式不规定演示主题。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_title: int
    max_items: int
    max_item_chars: int
    max_total_chars: int


LAYOUTS = {
    "cover": Limits(54, 0, 0, 100),
    "agenda": Limits(54, 8, 42, 380),
    "bullets": Limits(54, 6, 105, 550),
    "compare": Limits(54, 8, 85, 650),
    "end": Limits(54, 0, 0, 100),
    "chart": Limits(54, 3, 105, 350),
    "table": Limits(54, 2, 105, 250),
}
