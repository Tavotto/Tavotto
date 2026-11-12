## EPS can't do transparency: you are asked before exporting

PostScript has no transparency, so an EPS export of a figure with semi-transparent annotations used to fail afterwards with a message. Now, when EPS is ticked and an annotation on the figure is semi-transparent, clicking Export first asks what you want: export a PDF instead (EPS is swapped for PDF), export the EPS with those annotations fully opaque (colours unchanged; PDF, SVG and PNG keep the transparency), or cancel. Nothing changes when nothing is semi-transparent.
