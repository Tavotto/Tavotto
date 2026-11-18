## Scripts that import a helper module from a parent folder run right away again

If a script adds a parent folder to `sys.path` and then imports a helper module from it (for example a shared `figure_style.py`), Tavotto could stop before running with "1 library's source can't be determined yet" and wait for you to click Run anyway, every time, when your own Python has certain add-on hooks installed. Tavotto now runs these scripts first, as intended, and only asks about the module if the script really can't find it.
