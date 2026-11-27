## From data to figure no longer plots against the row number, and big tables always get a starting chart

Tables exported from R (and many from pandas) start with a column of row numbers, such as `rownames`, `index`, `No.` or `Unnamed: 0`. When no AI is connected, Tavotto used to put that column on the x axis, so the chart showed "value by row number", which means nothing. Tavotto now leaves such a column out of the default axes. It only does so when the name looks like a row number and the values are exactly 1, 2, 3 (or 0, 1, 2) down the rows, so a real "day" or "trial" column still works as an axis. You can still pick the column yourself from the axis drop-down.

For very large tables, Tavotto could end up with no chart at all, because the comparison and distribution charts it chose first can't be drawn at that size. It now falls back to a scatter plot or line chart of the table, so you always have a chart to start from; you can change the columns on the right.

Without AI, Tavotto now also decides which column goes on the x axis by looking at how its values behave. A column that looks like a setting or a time (a dose repeated across samples, or time points that restart for each subject) becomes the default x axis. A column that looks like a measurement becomes the default y axis. Before, it picked by position, so a table with the measurement first could come out with the axes swapped. Columns you pick yourself are never changed.

A column that only numbers the replicates (named `rep`, `replicate`, `重复` or `平行`) is also left out of the default axes, since "value by replicate number" is rarely what you want to see.

