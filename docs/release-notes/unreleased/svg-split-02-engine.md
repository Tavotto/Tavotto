## Explicit SVG bindings can recalculate a chart without replacing unrelated artwork

A closed chart model associates axes, series, data and legends explicitly. Matplotlib calculates the bound region and the compositor preserves unbound annotations and decoration. Model validation and coordinate examples are shared with the frontend. Fixed chart requests now use the existing worker pool, v1 envelopes, shared capacity accounting and sandbox guards. Python and workerd apply the same payload budgets and lifecycle; older supervisors fall back to the Python pool.

Charts rendered with `native-manifest/2` publish their display frame only inside `manifest.render_frame`; the one-point outward margin is a single contract constant pinned by golden vectors, and new frame fields require `native-manifest/3`. Older builds reject `/2` with `chart_model_unsupported` and keep the previous render.

Part of the #1873 split (RFC #1877, ADR 0182).
