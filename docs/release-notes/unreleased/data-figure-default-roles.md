## From data to figure no longer plots against the row number, and big tables always get a starting chart

Tables exported from R (and many from pandas) start with a column of row numbers, such as `rownames`, `index`, `No.` or `Unnamed: 0`. When no AI is connected, Tavotto used to put that column on the x axis, so the chart showed "value by row number", which means nothing. Tavotto now leaves such a column out of the default axes. It only does so when the name looks like a row number and the values are exactly 1, 2, 3 (or 0, 1, 2) down the rows, so a real "day" or "trial" column still works as an axis. You can still pick the column yourself from the axis drop-down.

For very large tables, Tavotto could end up with no chart at all, because the comparison and distribution charts it chose first can't be drawn at that size. It now falls back to a scatter plot or line chart of the table, so you always have a chart to start from; you can change the columns on the right.
