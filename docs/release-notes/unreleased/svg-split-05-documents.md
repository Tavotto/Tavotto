# Computational chart documents keep verified SVG sources after reopening

The proposed schema 4 storage namespace freezes exact SVG sources before committing the document. Reopening checks attachment identity and integrity, while older documents, autosave slots and versions retain their legacy addresses. Missing or altered sources fail explicitly, and the new routes retain session authentication.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR-XXXX.
