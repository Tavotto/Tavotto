## Stale or missing recovery hints fixed

**First click on "Edit figure content" for a refused PNG now shows the real reason.** Trigger: reopen a project and click edit on an original PNG that cannot be safely adopted. Before: a generic "couldn't render" message on the first click, the "Rebuild as editable figure" button only on the second. Now the first click shows the same card.

**Restoring a renamed script clears the canvas "script is gone" badge.** Trigger: rename a figure script, then rename it back and refresh the assets. Before: the asset card was fine but the canvas still said the script was missing.

**A script renamed back no longer stays listed as "to prepare".** Trigger: rename a script, dismiss the prepare card with "Later", restore the name and refresh. Refreshing now rechecks the project, so paths that no longer exist drop out; a card you already dismissed stays dismissed if nothing changed.

**Overwriting an export no longer leaves an old check report next to the new figure.** Trigger: export a PDF/PNG with the style-check report, then overwrite the same name without the report. The old `<name>_style-check.json` written by Tavotto is now removed; other files are never touched.
