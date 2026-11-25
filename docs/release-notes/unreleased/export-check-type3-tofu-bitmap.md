## Export results now point out Type 3 fonts, missing-glyph boxes and whole-bitmap figures

An unedited figure is exported from the file on disk, so a PDF could carry matplotlib's default Type 3 fonts (which journals such as IEEE and Elsevier send back), characters drawn as empty boxes because the author's machine had no Chinese font, or a scripted figure embedded as a single bitmap — and the export still reported no issues. The export result now checks the finished PDF: boxes are reported as a failed check, and Type 3 fonts or a scripted figure embedded as a bitmap are listed as things to watch for, naming the fonts and figures.

Simple TrueType and Type 1 fonts, whose missing-glyph boxes can't be told apart from real glyphs in a PDF, no longer mark a file as "not verified"; the check judges missing glyphs from the other fonts in the file.
