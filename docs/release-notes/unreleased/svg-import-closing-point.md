## Imported SVG paths no longer get a duplicate closing point

When an SVG drawn with relative coordinates closes back on its starting point, the imported path no longer keeps an extra point sitting on top of the first one. Paths are cleaner to edit and the path-quality check no longer reports a false zero-length segment.
