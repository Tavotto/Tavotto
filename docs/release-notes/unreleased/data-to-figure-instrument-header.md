## Data-to-figure now finds the real header row in instrument text exports

For `.txt` / `.dat` exports that start with a title, a note line such as `operator: lab`, the column names and a units line (`nm  a.u.`), the column names are now taken from the line next to the units, and the units line is no longer read as a row of data. Previously the note line was picked as the header, and the units line turned both numeric columns into "mixed" columns, so only histograms were offered.
