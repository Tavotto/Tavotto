## Data to figure: put several files with the same columns into one figure

When several data files share the same column names in the same order (one file per condition, say), Tavotto can now draw them in one figure with one group per file. The data files are only read; the new figure script is created next to them in a `tavotto_figures` folder, and it reads the original files every time it runs. Up to 8 files at once. If the files don't share the same columns, the request is refused with a clear message instead of drawing a misaligned figure.
