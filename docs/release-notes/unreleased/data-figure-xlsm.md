## Data-to-figure reads macro-enabled Excel workbooks

- `.xlsm` workbooks now load the same way as `.xlsx` files. Tavotto reads the cell values only; macros in the workbook are never opened or run. `.xls` and `.xlsb` files still need to be saved as `.xlsx` or CSV first.
