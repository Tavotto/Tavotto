## Data-to-figure reads more of the messy tables found in real projects

- Tables saved from Excel or WPS as CSV often carry a few empty columns at the right end (`a,b,c,,` on every row). Those trailing empty columns are now ignored and the table loads as usual. A nameless column that actually holds data, or a blank name in the middle of the header, is still reported instead of guessed.
- Excel workbooks that start with a title line (for example a plate-reader export with "OD600 2026-11-20" in A1, a blank row, then the column names) now load. Tavotto looks for the real header row the same way it already does for text files, but only when the first row cannot be a header; a workbook whose first row already works is read exactly as before.
