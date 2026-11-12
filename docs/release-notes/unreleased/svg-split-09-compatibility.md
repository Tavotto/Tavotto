# Existing figure edits preserve fonts and concurrent source changes

Font family names containing Fontconfig punctuation are passed to Matplotlib as literal names, with math-text weight and style preserved. Writing edits back to a script checks the source version again during verification, backup and replacement; an external edit is not overwritten by either publication or rollback.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR-XXXX.
