## Figure edits retain declared inputs

Declared inputs are captured through the existing preparation boundary, preserving the bytes actually read and the receipt for that execution. Missing or altered archived inputs are rejected rather than replaced with a same-name file.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
