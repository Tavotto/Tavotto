## Fewer false "check failed" results when an agent edits your drawings

When an agent or the assistant moves or restyles objects already on the canvas, the font check now only looks at what this edit changed (new objects, or text and font changes), instead of failing because an older label uses a different font. A frame drawn around a whole figure is no longer flagged for overlapping that figure's own labels, and a mistyped frame request now says what shape it expected.
