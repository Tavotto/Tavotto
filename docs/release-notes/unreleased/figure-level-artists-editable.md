## Shapes, lines and text placed directly on the figure can now be selected and edited

If your script draws on the figure itself instead of inside a subplot, for example with
`fig.add_artist(Polygon(..., transform=fig.transFigure))` to connect two panels, those elements could not
be clicked or edited before. This also affected lines, text, line collections and arrows added with
`fig.add_artist`, `ConnectionPatch`, images added with `fig.figimage`, and the titles, text, legends
and shapes inside `fig.subfigures(...)`. All of them now show up in the element list, can be selected,
and can have their style changed. Shapes, lines and text can also be dragged. Legends inside a subfigure
used to move only half as far as you dragged them. They now land where you drop them.
