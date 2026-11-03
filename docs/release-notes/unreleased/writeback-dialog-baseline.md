## Write back no longer overwrites an original that was swapped while the dialog was open

The "Write back to original file" dialog now remembers the original's modification time from the moment it opens. If the file is replaced by another program while the dialog is still open, confirming is refused with the usual "changed outside this tool" message and the file is left untouched. Close the dialog and open it again to write back against the new version. Restoring from the write-back records uses the same check.
