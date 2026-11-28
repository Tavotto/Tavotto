## A figure still opens when the script can't overwrite a file another program has open

If your script saves a side file into the project (for example the plotted data as a CSV) and that file is open in another program such as Excel, or is read-only, Windows refuses the write. Before, the whole run failed and the figure could not be edited. Now the figure renders and stays editable as usual; that one file is left untouched, and a small notice names it and says it wasn't written this time. Close the other program and run the figure again to write it.
