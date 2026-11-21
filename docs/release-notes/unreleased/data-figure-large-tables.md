## Data to figure: large text tables (up to 500 MB)

Text tables (CSV, TSV, `.txt`, `.dat`) larger than 5 MB are no longer refused. Tavotto now reads them in a streaming pass and, for line, step, point and scatter figures, thins the points before drawing so a long time series stays readable and still passes the data check. Summary-style figures (bars, boxes, histograms, error bars, fits), merged figures, Excel files and `.xvg` files keep the previous 5 MB limit for now. The "file too large" messages no longer quote a fixed number.
