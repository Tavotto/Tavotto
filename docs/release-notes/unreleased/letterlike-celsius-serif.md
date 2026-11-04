## ℃ and ℉ in canvas text keep their font after export

A text label set in a serif font used to show ℃ (and ℉, the Kelvin sign K and the Ångström sign Å) in a serif face on the canvas, but the exported PDF, SVG and images drew it in a sans-serif face. These symbols are now drawn as °C, °F, K and Å in the label's own font in both places, so the export matches the canvas. What you typed is unchanged: it is still ℃ in the saved project, and copying text out of the exported PDF still gives ℃ (the SVG's text layer reads °C, as it already does for composed superscripts).
