## The figure list no longer fails when a file disappears during a refresh

If a script was rewriting its output while Tavotto refreshed the project, a figure file could disappear between being listed and being read, and the whole figure list failed to load. That file is now skipped and the rest of the list loads normally.
