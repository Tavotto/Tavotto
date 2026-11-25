"""Editable manifest fields shared by native figure adapters."""

from __future__ import annotations


def marker_field(
    prop: str,
    value: str,
    options: list[str],
    shape: dict | None,
    original: dict | None = None,
    **extra,
) -> dict:
    """marker 这一族 enum 字段的唯一构造处（曲线 / 散点 / 茎叶 / 图例示意）。

    `marker_current` 是**只读事实**，不是 override 的落点：`value` 说的是
    「用户选中的是哪个取值」，它说的是「图上此刻画的是什么形状」。有
    override 时它自然就是 override 之后的形状——manifest 本来就是渲染态。

    `marker_original` 是同一套五档结构的第二个只读事实：**override 之前**
    那个形状。**没有 override 时它整个不出现**——缺席的含义就是「与
    `marker_current` 相同」，前端不用再判一次「改没改过」。

    引擎说不出形状时**这两个键都整个不出现**：「不知道」与「这个对象没有
    标记」（`{"kind": "none"}`）是两个不同的答案，合并进同一档的话老引擎发来
    的清单会被读成「图上没有标记」。
    """
    field = {"prop": prop, "type": "enum", "value": value, "options": options, **extra}
    if shape is not None:
        field["marker_current"] = shape
    if original is not None:
        field["marker_original"] = original
    return field


def point_size_field(value: float) -> dict:
    """Nominal marker area at the source figure size."""
    return {
        "prop": "size",
        "type": "number",
        "value": value,
        "min": 1,
        "max": 400,
        "step": 1,
        "unit": "pt²",
    }


def opacity_field(value: float) -> dict:
    return {"prop": "alpha", "type": "number", "value": value, "min": 0, "max": 1, "step": 0.05}
