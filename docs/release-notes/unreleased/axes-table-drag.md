## Tables added with `ax.table` can now be dragged

A table placed on a plot with `ax.table(...)` could be shown or hidden but not moved; dragging it only said it wasn't supported. It now drags like a shape, cells and text together, and the move can be undone. Tables placed below a subplot (`loc="bottom"`) can be moved without pushing the other subplots around.
