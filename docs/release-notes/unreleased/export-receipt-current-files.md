## Export receipt now lists only the files this export wrote

When you exported every figure separately right after an earlier export in the same project, the result could list files from the earlier export, leave out one figure's files, and make "Show in folder" look for a file that does not exist in the new folder. The receipt now counts only the files each figure's export actually wrote, and a single export after a per-figure batch no longer shows the batch's old list. A failed figure with no error detail now shows a plain "export failed" line instead of a raw `{{error}}` placeholder.
