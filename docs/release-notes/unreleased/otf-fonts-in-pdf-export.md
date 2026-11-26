## PDFs exported with an .otf font no longer carry a broken font

When a figure used an OpenType (.otf, CFF-based) font, the exported PDF embedded it in a slot meant for TrueType fonts. Some viewers and journal submission checkers then showed wrong glyphs or rejected the file. Tavotto now converts such fonts to TrueType when it writes the PDF, and the export check reports any file where the font type and the embedded font program disagree.
