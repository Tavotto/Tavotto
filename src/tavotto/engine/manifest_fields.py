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


def fill_color_field(color: str) -> dict:
    return {"prop": "facecolor", "type": "color", "value": color}


def axis_facecolor_field(color: str) -> dict:
    return {**fill_color_field(color), "group": "网格与边框"}


def axis_limits_field(axis: str, limits) -> dict:
    return {
        "prop": f"{axis}lim",
        "type": "pair",
        "value": list(map(float, limits)),
        "group": "数据范围",
    }


def axis_scale_field(axis: str, mode: str, options: list[str], **extra) -> dict:
    return {
        "prop": f"{axis}scale",
        "type": "enum",
        "value": mode,
        "options": options,
        "group": "数据范围",
        **extra,
    }


def axis_invert_field(axis: str, inverted: bool) -> dict:
    return {
        "prop": f"invert_{axis}",
        "type": "bool",
        "value": bool(inverted),
        "group": "数据范围",
    }


def axis_grid_fields(values: dict) -> list[dict]:
    """Major grid visibility by axis and strokes shared by both grid levels."""
    group = "网格与边框"
    return [
        {"prop": "grid_x", "type": "bool", "value": bool(values["grid_x"]), "group": group},
        {"prop": "grid_y", "type": "bool", "value": bool(values["grid_y"]), "group": group},
        {"prop": "grid_color", "type": "color", "value": values["grid_color"], "group": group},
        {
            "prop": "grid_linestyle",
            "type": "enum",
            "value": str(values["grid_linestyle"]),
            "options": ["-", "--", ":", "-."],
            "group": group,
        },
        {
            "prop": "grid_linewidth",
            "type": "number",
            "value": round(float(values["grid_linewidth"]), 2),
            "min": 0.1,
            "max": 10,
            "step": 0.1,
            "unit": "pt",
            "group": group,
        },
        {
            "prop": "grid_alpha",
            "type": "number",
            "value": round(float(values["grid_alpha"]), 2),
            "min": 0,
            "max": 1,
            "step": 0.05,
            "group": group,
        },
    ]


AXIS_FRAME_SIDES = ("top", "right", "bottom", "left")


def axis_frame_fields(values: dict) -> list[dict]:
    """All-side and per-side edit affordances for a Cartesian frame."""
    group = "网格与边框"
    fields = [
        {"prop": prop, "type": "bool", "value": bool(values[prop]), "group": group}
        for prop in (f"spine_{side}" for side in AXIS_FRAME_SIDES)
    ]
    fields.extend(
        (
            {
                "prop": "spine_color",
                "type": "color",
                "value": values["spine_color"],
                "group": group,
            },
            {
                "prop": "spine_linewidth",
                "type": "number",
                "value": round(float(values["spine_linewidth"]), 2),
                "min": 0.1,
                "max": 10,
                "step": 0.1,
                "unit": "pt",
                "group": group,
            },
        )
    )
    section = "边框（逐条）"
    for side in AXIS_FRAME_SIDES:
        fields.extend(
            (
                {
                    "prop": f"spine_{side}_color",
                    "type": "color",
                    "value": values[f"spine_{side}_color"],
                    "group": section,
                },
                {
                    "prop": f"spine_{side}_linewidth",
                    "type": "number",
                    "value": round(float(values[f"spine_{side}_linewidth"]), 2),
                    "min": 0.1,
                    "max": 10,
                    "step": 0.1,
                    "unit": "pt",
                    "group": section,
                },
            )
        )
    return fields


def errorbar_stroke_fields(color: str | None, width: float) -> list[dict]:
    """Stroke fields whose values are independent of the native cap geometry."""
    return [
        *([{"prop": "color", "type": "color", "value": color}] if color is not None else []),
        {
            "prop": "linewidth",
            "type": "number",
            "value": round(float(width), 2),
            "min": 0.1,
            "max": 5,
            "step": 0.1,
            "unit": "pt",
        },
    ]


def errorbar_cap_fields(length: float, width: float) -> list[dict]:
    return [
        {
            "prop": "capsize",
            "type": "number",
            "value": round(float(length), 2),
            "min": 0,
            "max": 15,
            "step": 0.5,
            "unit": "pt",
        },
        {
            "prop": "cap_thickness",
            "type": "number",
            "value": round(float(width), 2),
            "min": 0.1,
            "max": 5,
            "step": 0.1,
            "unit": "pt",
        },
    ]


def bar_paint_fields(values: dict) -> list[dict]:
    """Bar paint supported by the source, with stroke width in points."""
    fields = [
        {"prop": prop, "type": "color", "value": values[prop]}
        for prop in ("facecolor", "edgecolor")
        if prop in values
    ]
    if "linewidth" in values:
        fields.append(
            {
                "prop": "linewidth",
                "type": "number",
                "value": round(float(values["linewidth"]), 2),
                "min": 0,
                "max": 5,
                "step": 0.1,
                "unit": "pt",
            }
        )
    return fields


def bar_width_field(width: float, *, horizontal: bool = False) -> dict:
    return {
        "prop": "bar_thickness" if horizontal else "bar_width",
        "type": "number",
        "value": round(float(width), 3),
        "min": 0.01,
        "max": 5,
        "step": 0.02,
        "unit": "数据单位",
    }
