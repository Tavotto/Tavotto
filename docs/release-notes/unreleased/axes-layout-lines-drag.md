## Leader lines drawn in axes coordinates can now be dragged

A line added to a plot with `transform=ax.transAxes` (for example a dashed leader line from a map point to its label) was treated as a data curve: you could change its colour, width and dash style, but dragging it did nothing. Lines whose position does not depend on the data (axes, figure or inch coordinates) can now be dragged like shapes, and the move can be undone. Data curves, `axhline` / `axvline`, and lines offset from data coordinates still stay put.
