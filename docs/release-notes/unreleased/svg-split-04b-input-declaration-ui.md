## Declare the inputs a figure run should use

Before running a script you can now open the preparation card's details and declare which inputs the run should read, choosing from the inputs already approved for the project. The declaration is re-checked through the existing preparation flow: cancelling writes nothing, a stale scope cannot be saved as a new fact, and the frozen summary always reflects what the backend actually captured.

This is part of the #1873 split. Architecture and release approval remain pending in [Issue #1877](https://github.com/Tavotto/Tavotto/issues/1877) and ADR 0182.
