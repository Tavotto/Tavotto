## Saving keeps the document and its project file in step

Save receipts, autosave slots and project-file bindings now carry the document format and the project generation they belong to. A save reply that arrives after you switched documents is dropped instead of being applied to the newer one, and recovery copies stay tied to the document that produced them.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
