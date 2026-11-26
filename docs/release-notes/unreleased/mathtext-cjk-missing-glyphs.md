## Chinese inside a math-mode label is reported as missing glyphs

A label such as `浓度 ($\mu$M)` is drawn by matplotlib's math renderer, which uses one font for the plain characters and has no Chinese fallback, so the Chinese came out as empty boxes in the exported PDF while the pre-submission check said nothing. The check now reads such labels the way matplotlib draws them and flags the missing characters.
