## Recover incomplete Matplotlib font indexes

When Matplotlib's cached font list omits its default font even though the font is installed, safe rendering can now recover the font inventory without changing the existing cache. Previously, figures using the default font could fail to open for editing.
