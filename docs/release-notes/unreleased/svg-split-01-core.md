# Static SVGs retain their scene geometry through RenderCore

The proposed SVG foundation records stable nodes, physical dimensions and nested transforms, and translates supported paths, text, images, references, clips and opacity into the existing RenderCore representation. Unsupported expressions produce explicit errors. CJK fallback uses the resolved font family and weight.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR-XXXX.
