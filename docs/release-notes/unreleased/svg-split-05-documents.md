## Computational chart documents use the same storage as schema 3

Schema 4 documents are stored in the same directories, appear in the same document list and share the same version history as schema 3; only the document's schema number distinguishes them. Adding chart state to an existing document upgrades it in place under the same document identity, autosave slot and timeline, so its version history is kept instead of forking a second copy. (The chart-creation entry point and the pre-upgrade timeline backup it must perform land with the editor work, not with this storage change.)

Saving freezes exact SVG sources before the document body is committed in one atomic write, and reopening checks attachment identity and integrity. Project packages carry those source blobs and install them into the target project on import, so an imported chart document can be saved again. Missing or altered sources fail explicitly, and the document routes keep session authentication.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
