## Figures that use macOS Helvetica at small sizes open again

A script that sets the font to the system Helvetica on a Mac at 5 to 7 pt (the usual Nature setup) failed to open with "failed to load glyph" or "division by zero", even though the same script ran fine from the terminal. Tavotto now falls back to unhinted text only for the few sizes where the font file cannot be loaded, and your figure opens, previews and exports as usual.
