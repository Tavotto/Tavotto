## A project-file confirmation is bound to the document it was made for

Saving into an existing project file now records which autosave slot revision the write actually produced, and the confirmation endpoints re-read that revision instead of trusting the value the client sent. A confirmation that arrives after the file changed is rejected instead of being applied to a newer document, and opening a file reports the binding it actually has.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
