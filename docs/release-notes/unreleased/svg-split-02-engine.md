## Explicit SVG bindings can recalculate a chart without replacing unrelated artwork

A closed chart model associates axes, series, data and legends explicitly. Matplotlib calculates the bound region and the compositor preserves unbound annotations and decoration. Model validation and coordinate examples are shared with the frontend. Fixed chart requests now use the existing worker pool, v1 envelopes, shared capacity accounting and sandbox guards. Python and workerd apply the same payload budgets and lifecycle; older supervisors fall back to the Python pool.

This is part of the #1873 split. Format contracts and release qualification remain under review in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
