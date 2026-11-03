## Several wrong or missing hints fixed after Windows acceptance testing

**Write-back result no longer says "no edits to write back".** Trigger: after
"Write back to original file" succeeds, the result dialog listed the updated
files but its header still read "No figure edits to write back yet". The
header is now hidden on the success result.

**Restoring from the write-back records names the real backup folder.**
Trigger: the project has a custom backup folder. Before: the confirmation
always said backups go to `cache/original_backups`. Now it shows the folder
the backup is actually written to.

**A missing figure script is no longer reported as "script updated".**
Trigger: the script behind a figure was renamed or deleted and "Rebuild"
failed. Before: the properties panel said the script had been updated and
would rebuild on edit. Now it says the source script is missing.

**A script that fails for lack of LaTeX shows the specific explanation.**
Trigger: `text.usetex` with no `latex` on PATH (or a missing TeX package).
Before: the prepare card only said the run failed. Now it shows the
explanation that this is a system TeX tool, not a Python package.

**A pinned Python that lacks packages lists which ones.** Trigger: a Python
pinned in Settings is missing modules the script imports. The card now names
them on one line instead of only saying components are missing.

**"Project status" no longer says there are no figures when runtime-captured
figures exist.** Trigger: a project whose figures all come from running
scripts. The empty state now says how many runtime figures there are.

**Editing reference: `annotate()` arrows.** The guide said every `annotate()`
arrow was style-only. Arrows with empty text whose endpoint coordinates can
be converted back can be dragged whole or by either end; only arrows with
text or non-convertible coordinates stay style-only.
