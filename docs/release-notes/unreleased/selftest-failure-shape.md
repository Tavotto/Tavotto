## Diagnostic packages say why a Tavotto-managed environment failed its final check

When installing missing packages into a Tavotto-managed environment finishes but the final self-test fails, the diagnostic package now records which step failed (could not start, timed out, returned an error, or exited without replying), the exit code, the stable error code, the built-in exception type and frame categories, and the Tavotto engine source line where it failed. Error text, paths and file names are still left out. The diagnostic package README says so. Bundle schema is now 16.
