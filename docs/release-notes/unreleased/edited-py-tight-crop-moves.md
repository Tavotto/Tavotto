## Exporting the edited .py works again after moving text on a tightly cropped figure

If your script saves its figure with `bbox_inches="tight"` and you moved a text label, axis label, title or legend in Tavotto, exporting the edited `.py` failed with "the edited script doesn't reproduce the figure". The edited script placed the moved items relative to the uncropped figure instead of the cropped page you see in Tavotto, so everything moved by the width of the cropped margin. Moved items now land exactly where they are in Tavotto, and the edited script is exported.
