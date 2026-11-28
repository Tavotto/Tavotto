## Data-to-figure now reads tables whose first header cell is empty

Tables exported with pandas `df.to_csv()` (or R `write.csv`, or an Excel sheet with an empty A1) have a blank name above the row-index column, and used to be rejected as having an invalid header. That first column is now simply called `col1` and the table loads as usual. Only the first column gets this treatment: blank names elsewhere in the header, or duplicate names, are still reported, because they usually mean the chosen row is not the header.
