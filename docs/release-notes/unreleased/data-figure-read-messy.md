## Data-to-figure reads more of the messy tables found in real projects

- Tables saved from Excel or WPS as CSV often carry a few empty columns at the right end (`a,b,c,,` on every row). Those trailing empty columns are now ignored and the table loads as usual. A nameless column that actually holds data, or a blank name in the middle of the header, is still reported instead of guessed.
