## Figure edits retain declared inputs and the execution they came from

Declared inputs are captured through the existing preparation and worker boundaries, preserving the bytes actually read and the receipt for that execution. Historical source records stay distinct from current execution authority. Missing or altered archived inputs are rejected rather than replaced with a same-name file. Python and Rust control-plane consumers use the same contract.

## Editing a figure stays on the run it came from

When you edit a figure produced by a script, Tavotto now remembers the exact run that drew it and keeps applying your edits to that run while you continue editing. Saving, reopening, preflight and export all keep the same change; a different script with the same file name can no longer be mistaken for the one that produced the figure. An output snapshot saved earlier is only treated as an archive of what you saw, not as permission to edit a run that is no longer there.

## A moved or deleted script says so instead of naming a package

Running a figure whose script file has been moved or deleted used to report that a package named after the script was missing, which sent you looking for something to install. It now says the script file itself is missing and asks you to put it back. Tavotto never substitutes another file with the same name.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
