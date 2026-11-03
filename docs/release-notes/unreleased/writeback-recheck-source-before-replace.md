## Write-back now re-checks the source images right before backing up and replacing them

Writing an edited figure back to its original PDF/PNG replays the script first, which can take minutes. If another program replaced the original image during that replay, Tavotto could back up the newer version and then overwrite it. The write-back now re-verifies every target (modification time and size) before the backup and again before each replace; if anything changed it stops with the usual "source changed" message, leaves your files untouched (rolling back any already-replaced sibling), and keeps no leftover temporary files.

Scripts that save a PNG through an open file handle into the project (so the replay itself rewrites that file) no longer trip this check; only files the script did not write itself are compared against the version you saw.
