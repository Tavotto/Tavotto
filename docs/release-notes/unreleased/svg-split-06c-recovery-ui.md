## Recovery and previews stay with the document that produced them

Reopening a project now shows recovery candidates, version thumbnails and previews that belong to the document and generation they were captured for. A stale preview from an earlier document is no longer offered as the current one, and the inspector's original-file actions report what the document actually holds.

## Validation points at the panel that has the problem

Problem navigation and preflight now resolve issues against the current source facts, so a warning raised by an older render no longer highlights a panel that has since been fixed.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
