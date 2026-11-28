## Windows history recovery preserves committed sessions and respects deletions

When recovering AI history from an older Windows package, Tavotto no longer treats a database copy that might omit committed changes as a successful recovery. If recovery needs to retry another package, sessions already recovered and then deleted stay deleted.
