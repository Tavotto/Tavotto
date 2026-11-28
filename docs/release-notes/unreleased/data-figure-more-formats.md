## Data-to-figure opens a few more kinds of data files

- Plain-text exports ending in `.prn` or `.xy` (common for instruments and diffraction scans) are now read the same way as `.txt` and `.dat` files.
- JSON Lines files (`.jsonl` / `.ndjson`, one flat record per line) now load as a table: each key becomes a column, and a record that lacks a key gets an empty cell. Files with nested objects or arrays, repeated keys, or non-standard values such as `NaN` are reported as unreadable rather than guessed.
