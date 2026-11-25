## Bind and recompute an SVG panel without replacing its artwork

SVG panels that carry a closed chart model can now be inspected, bound and recomputed through dedicated endpoints. Tavotto only replaces the regions you explicitly bound; the rest of the artwork keeps its original geometry, and the response reports the engine identity that produced it. Nothing is inferred from appearance, and an ordinary SVG stays an ordinary SVG until you bind axes and series yourself.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
