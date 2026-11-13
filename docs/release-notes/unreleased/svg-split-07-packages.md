## Portable chart packages verify their resources before installation

The proposed package contract checks exact SVG sources, data, declared inputs and receipts, rejecting duplicate members, path traversal, excessive resources and mismatched identities. Inspection, installation, relocation and adoption remain separate steps. Archived paths and past execution receipts do not grant permission to run scripts on the receiving machine.

## Install computational-chart project packages in a new folder

The project menu and project list can inspect a computational-chart project package and install its resources into a new folder. Opening the installed project preserves your previous draft and saves a working copy through the normal save flow. If you close the installation window while save confirmation is pending, reopening the installation record continues with your latest draft instead of replacing it with the package's original layout. Installation does not run the included scripts.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
