## Chart data parsing follows the shared data-table rules

Pasted tables, CSV data and numeric grids now use the same cell rules as Tavotto's data-to-figure pipeline. `1_000` is read as the number 1000 instead of being rejected as non-numeric. `NaN` / `nan` is treated as a missing value, so a column containing it is no longer offered as a text or category column. `Infinity` and overflowing literals such as `1e999` are reported as non-finite values instead of being accepted or reported as plain text.

Tables and grids over 100,000 rows or 64 columns are rejected as a whole with `data_too_long` / `data_too_wide`; they are never read partially.
