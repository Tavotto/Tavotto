## Portable chart packages verify their resources before installation

The package contract checks exact SVG sources, data, declared inputs and receipts, rejecting duplicate members, path traversal, excessive resources and mismatched identities. Inspection and installation are separate steps on the existing `/api/package*` endpoints; relocation and adoption are not part of this change. Archived paths and past execution receipts do not grant permission to run scripts on the receiving machine.

## Install computational-chart project packages in a new folder

“Import project package” is one entry (project menu, canvas drop and project list). The backend decides from the package's own content whether it is an older layout package or a computational-chart package, and the same flow can inspect the chart package and install its resources into a new folder. Opening the installed project preserves your previous draft and saves a working copy through the normal save flow. If you close the installation window while save confirmation is pending, reopening the installation record continues with your latest draft instead of replacing it with the package's original layout. Installation does not run the included scripts.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
