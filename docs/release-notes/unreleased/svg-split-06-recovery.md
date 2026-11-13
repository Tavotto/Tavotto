## Saving and recovery keep the document and project file they belong to

Project-file revisions, autosave receipts, previews and timeline snapshots retain their document format and project generation. Switching projects or restoring an older document cannot attach an old response to the new working copy. If a later choice supersedes save-target confirmation, the error offers the existing Open document flow, guarded against a subsequent project or document switch.

## Saving is not blocked by an unreadable project-file binding

If the server cannot answer the project-file metadata request, the app now treats an unrecognised reply as an older server rather than as a temporary outage. Automatic saves and the last save before switching documents or projects keep working, so the last edit is not lost. A reply that claims the newer contract but cannot be read still fails closed.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
