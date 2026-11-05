## Export an edited copy of your script

The export dialog has a new PY format. It saves `<name>_edited.py` with your edits written into the script as matplotlib calls, so `python` reproduces the figure without Tavotto. Your original script is never touched. Tavotto re-runs the new script and compares it with the figure before handing it over; edits it can't express are listed, and if the result doesn't match, the .py isn't exported. Works for single figures from scripts that call `savefig` or `plt.show`.
