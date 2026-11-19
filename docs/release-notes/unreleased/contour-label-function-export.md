## Contour plots whose labels use a formatting function now export as editable SVG

A figure that labels its contour lines with a function, such as `ax.clabel(cs, fmt=lambda v: f"{v:.1f}")`, used to fail every export with "SVG pairing is unavailable for custom code in the figure (custom instance attribute QuadContourSet.labelFmt)". Matplotlib calls the formatting function only while `clabel` creates the label text. It never calls it while drawing, so Tavotto no longer blocks the PDF + SVG pair for it. Other custom code on the same contour object is still blocked as before.
