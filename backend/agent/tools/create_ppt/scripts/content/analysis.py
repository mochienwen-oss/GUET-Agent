"""Small deterministic table operations with explicit source and scope."""
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from agent.tools.create_ppt.scripts.content.materials import Material


def _number(value):
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("数值字段存在空值，不能按0计算")
    try:
        result = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        raise ValueError("数值字段包含非数字：%s" % str(value)[:60]) from None
    if not result.is_finite():
        raise ValueError("数值必须有限")
    return result


def _text(value):
    return format(value.normalize(), "f") if value else "0"


def analyse(material: Material, spec: dict) -> dict:
    operation = spec.get("operation")
    if operation not in {"count", "sum", "mean", "share"}:
        raise ValueError("仅支持计数、求和、均值和占比")
    if not material.columns:
        raise ValueError("所选材料不是数据表")
    columns = material.columns
    field, group, denominator = (spec.get(k) for k in ("field", "group_by", "denominator_field"))
    filters = spec.get("filters") or {}
    required = [*filters, *([group] if group else []), *([field] if field else [])]
    if operation != "count" and not field:
        raise ValueError("请选择数值字段")
    if operation == "share":
        if not denominator:
            raise ValueError("计算占比需明确分母字段，不能猜测分母")
        if spec.get("denominator_aggregation") not in {"sum", "single"}:
            raise ValueError("请明确分母口径：逐行合计，或组内同一总量")
        required.append(denominator)
    if any(key not in columns for key in required):
        raise ValueError("分析字段不在原数据表中")
    selected = [dict(zip(columns, row)) for row in material.rows]
    selected = [row for row in selected if all(str(row[k]) == str(v) for k, v in filters.items())]
    if not selected:
        raise ValueError("筛选后没有数据")
    grouped = OrderedDict()
    for row in selected:
        grouped.setdefault(str(row[group]) if group else "全部", []).append(row)
    values, denominators = [], []
    for rows in grouped.values():
        if operation == "count":
            result = Decimal(len(rows))
        else:
            numbers = [_number(row[field]) for row in rows]
            total = sum(numbers, Decimal(0))
            if operation == "mean":
                result = total / len(numbers)
            elif operation == "share":
                raw_denominators = [_number(row[denominator]) for row in rows]
                if spec["denominator_aggregation"] == "single":
                    if len(set(raw_denominators)) != 1:
                        raise ValueError("分母口径选择同一总量，但组内总量不一致；请检查分组或选择合计")
                    divisor = raw_denominators[0]
                else:
                    divisor = sum(raw_denominators, Decimal(0))
                if divisor <= 0:
                    raise ValueError("分母必须大于0")
                denominators.append(_text(divisor))
                result = total / divisor * 100
            else:
                result = total
        values.append(_text(result))
    unit = "%" if operation == "share" else spec.get("unit") or material.units.get(field or "", "")
    return {"analysis_id": uuid4().hex[:12], "material_id": material.material_id,
            "operation": operation, "field": field, "group_by": group, "filters": filters,
            "unit": unit, "denominator_field": denominator, "denominators": denominators,
            "denominator_aggregation": spec.get("denominator_aggregation"),
            "categories": list(grouped), "values": values, "row_count": len(selected),
            "chart_type": spec.get("chart_type", "bar"),
            "label": spec.get("label") or (field or "记录数"), "scope": material.scope,
            "locator": material.locator, "source_title": material.title}
