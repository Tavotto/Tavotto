## Declared inputs travel with the execution that read them

Declared inputs now follow the same execution boundary as the figure itself: the bytes actually read and the receipt for that run are frozen together, and the Python and Rust control planes agree on that record. A cached build cannot be re-labelled with a newer runtime, and an archived input set is never replaced by a same-name file.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
