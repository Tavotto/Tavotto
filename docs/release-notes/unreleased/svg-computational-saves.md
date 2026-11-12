## Editing a figure stays on the run it came from

When you edit a figure produced by a script, Tavotto now remembers the exact run that drew it and keeps applying your edits to that run while you continue editing. Saving, reopening, preflight and export all keep the same change; a different script with the same file name can no longer be mistaken for the one that produced the figure. An output snapshot saved earlier is only treated as an archive of what you saw, not as permission to edit a run that is no longer there.

## A moved or deleted script says so instead of naming a package

Running a figure whose script file has been moved or deleted used to report that a package named after the script was missing, which sent you looking for something to install. It now says the script file itself is missing and asks you to put it back. Tavotto never substitutes another file with the same name.

## Saving is not blocked by an unreadable project-file binding

If the server cannot answer the project-file metadata request, the app now treats an unrecognised reply as an older server rather than as a temporary outage. Automatic saves and the last save before switching documents or projects keep working, so the last edit is not lost. A reply that claims the newer contract but cannot be read still fails closed.
