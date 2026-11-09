## Intermediate files during script conversion

When a converted script writes an intermediate file and then checks it with `pathlib`,
conversion validation can now see the temporary file and continue. Validation still
keeps the original project files unchanged.
