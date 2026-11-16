## Static SVGs retain their scene geometry through RenderCore

The proposed SVG foundation records stable nodes, physical dimensions and nested transforms, and translates supported paths, text, images, references, clips and opacity into the existing RenderCore representation. Unsupported expressions produce explicit errors. CJK fallback uses the resolved font family and weight.

Part of the #1873 split (RFC #1877, ADR 0182).
