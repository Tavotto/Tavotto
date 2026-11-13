"""Derived computed frame composition. No source execution or geometry guessing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tavotto.engine import chartframe
from tavotto.rendercore import svgir
from tavotto.svgcoord import apply, compose, invert, node_matrix

NS = "{http://www.w3.org/2000/svg}"


def freeze_source_presentation(root, resolver):
    """Pin winners on the derived original tree; preserve use-instance inheritance."""
    source_nodes = set(root.iter())
    parents = {c: p for p in root.iter() for c in p}
    rows = {}

    def walk(node, parent):
        style = resolver._style(node, parent)
        rows[node] = (resolver.cascade_declarations(node), style)
        for child in node:
            walk(child, style)

    walk(root, svgir._DEFAULT)

    def definition(node):
        while node is not None:
            if node.tag in {NS + "defs", NS + "symbol"}:
                return True
            node = parents.get(node)
        return False

    for node, (own, computed) in rows.items():
        if node.tag == NS + "style":
            node.text = ""
            continue
        declarations = {}
        for key, value in own.items():
            if key.startswith("{") or (node is root and key == "overflow"):
                continue
            if value == "inherit" and key not in svgir._INHERITED and not definition(node):
                value = computed.get(key, "initial")
            declarations[key] = value
        node.set(
            "style", ";".join(f"{key}:{value} !important" for key, value in declarations.items())
        )
    return source_nodes


def envelope(scene, source_nodes, raw_frame, *, source_size, source_mapping, source_view_box):
    """Original source viewport is retained; only generated furniture uses F."""
    frame = chartframe.validate_frame(raw_frame)
    if any(abs(a - b) > 1e-7 for a, b in zip(frame["source_viewport_pt"], source_size)):
        raise ValueError("computed frame source viewport mismatch")
    left, top, width, height = frame["rect_pt"]
    ids = {n.get("id") for n in scene.iter() if n.get("id")}
    serial = 0

    def new_id():
        nonlocal serial
        while True:
            value = f"tavotto_frame_clip_{serial}"
            serial += 1
            if value not in ids:
                ids.add(value)
                return value

    for node in scene.iter():
        if "data-tavotto-native-viewport-pt" in node.attrib:
            node.set(chartframe.MARKER, " ".join(map(str, frame["rect_pt"])))
    defs = ET.SubElement(scene, NS + "defs")

    def native(node):
        return any("data-tavotto-native-viewport-pt" in n.attrib for n in node.iter())

    def protect(parent):
        for child in list(parent):
            if child.tag in {NS + "defs", NS + "style", NS + "metadata", NS + "title", NS + "desc"}:
                continue
            if child not in source_nodes:
                continue
            if native(child):
                protect(child)
                continue
            # Parent-local clip derives from the original physical viewport,
            # including nonzero source viewBox and rotated ancestor transforms.
            local_to_pt = compose(node_matrix(scene, parent.get("id")), source_mapping)
            inverse = invert(local_to_pt)
            sw, sh = source_size
            points = [apply(inverse, x, y) for x, y in [(0, 0), (sw, 0), (sw, sh), (0, sh)]]
            clip_id = new_id()
            cp = ET.SubElement(defs, NS + "clipPath", {"id": clip_id})
            ET.SubElement(
                cp, NS + "path", {"d": "M " + " L ".join(f"{x} {y}" for x, y in points) + " Z"}
            )
            wrapper = ET.Element(NS + "g", {"clip-path": f"url(#{clip_id})"})
            i = list(parent).index(child)
            parent.remove(child)
            wrapper.append(child)
            parent.insert(i, wrapper)

    protect(scene)
    # Retain the original root viewport/percent basis and inherited context.
    scene.set("x", "0")
    scene.set("y", "0")
    scene.set("width", str(source_size[0]))
    scene.set("height", str(source_size[1]))
    if "viewBox" not in scene.attrib:
        # A standalone SVG without viewBox uses CSS px (96/in), not physical
        # pt. Keep the original resolver's implicit viewport when nesting it.
        scene.set("viewBox", " ".join(map(str, source_view_box)))
    scene.set("overflow", "visible")
    outer = ET.Element(
        NS + "svg",
        {
            "width": f"{width}pt",
            "height": f"{height}pt",
            "viewBox": f"{left} {top} {width} {height}",
            "preserveAspectRatio": "none",
        },
    )
    mirror = scene.attrib.pop("data-tavotto-svg-targets", None)
    if mirror is not None:
        outer.set("data-tavotto-svg-targets", mirror)
    outer.append(scene)
    return outer
