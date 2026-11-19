## Scripts inside a package that use relative imports now run

If a script lives in a package (a folder with `__init__.py`) and imports its neighbours with `from .helpers import tidy`, running it as a plain file always failed with "attempted relative import with no known parent package". Tavotto now recognises this shape and runs the script the way `python -m plots.fig` would, in both the safe and the real-Python modes, while keeping the figure under the same script name as before. Scripts that already run, and the `tavotto run` terminal command, are unchanged.
