## SVG in and out, and text to outlines

Paste SVG onto the canvas to get editable shapes, paths and text, and use "Break into editable objects" on a static SVG figure. Anything that cannot be brought in faithfully stays as an image, with the reason shown. Text can be converted to outlines (Shift-⌘O) using the same font the export uses, and "Copy as SVG" puts the selection on the clipboard using the same writer as the SVG export. External SVG is always read by Tavotto's restricted parser, which refuses external entities, external links, recursive references and oversized input.
