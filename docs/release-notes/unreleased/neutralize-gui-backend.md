## Scripts that ask for a desktop plotting window now run and show their figures

Scripts that start with a line such as `matplotlib.use("TkAgg")` or `"Qt5Agg"` used to fail when the Python in use had no desktop window toolkit installed, and switching Python did not help. Tavotto never opens windows, so it now keeps a non-interactive backend in that case, whether the script runs in the safe mode or in your own Python, and the figure appears in the editor as usual. Scripts that choose a file-based backend such as `pdf` or `svg` are left exactly as they are.
