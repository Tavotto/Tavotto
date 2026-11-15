## Existing figure edits keep fonts literal and never overwrite a concurrent edit

When a figure's font family contains Fontconfig punctuation (for example a colon), write-back now passes it to Matplotlib as a literal family name instead of letting it select a file. Math text keeps its bold or italic weight and style.

While an edit is being written back, the source file's content is checked before the backup and before each replacement. If the file changed in the meantime — even with the same length and nanosecond modification time — the write-back stops and reports that the original changed, leaving the external version untouched. If the source cannot be read while the write-back is being prepared, it now stops and reports that the original could not be verified instead of skipping the check. Verification reads the whole source file each time, so write-back of very large files takes longer.

Follow-up to #1873; tracked in #1877.
