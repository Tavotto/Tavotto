## Data to figure: GROMACS .xvg files can be dropped in

Data to figure now reads GROMACS / Grace `.xvg` files. Column names are taken from the file's axis labels and legends (`gmx energy`, `gmx rms` and similar work as they are), and common Grace markup in those labels (superscripts, subscripts, Greek letters) is turned into plain text. If a file holds several data sets separated by `&`, only the first one is drawn. The `.xvg` file itself is only read, never changed.
