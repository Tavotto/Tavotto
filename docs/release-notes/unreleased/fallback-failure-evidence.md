## Clearer diagnostics when a fallback run also fails

When a protected file operation blocks one run and its fallback also fails, the
diagnostic report can retain the observed operation categories and the uncaught
exception category for their respective attempts, when available. These facts
contain no file paths or exception text and do not determine whether a missing
file was an input or an output.
