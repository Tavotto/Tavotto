## Opening a figure for the first time shows which step it is on

The first time you open a figure for editing, Tavotto has to find the Python environment, start Python and run the script, which can take tens of seconds. The element list and the inspector now say which of the three steps it is on (finding the Python environment, starting Python, then running the script and building the editable elements) next to the time elapsed, instead of a single "Building the figure…" line that looked stuck. A quick redraw of a figure that is already open still just says it is building.
