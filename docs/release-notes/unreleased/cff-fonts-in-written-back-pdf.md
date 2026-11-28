## Updating the original figure keeps Chinese text selectable with PingFang, Hiragino and Noto CJK fonts

When a figure used a CFF-based font, such as PingFang or Hiragino Sans GB on macOS, Noto Sans CJK or Source Han Sans on Linux, or any .otf font, writing an edit back to the original PDF embedded that font in a slot meant for TrueType fonts. Some viewers then drew the wrong glyphs, and the Chinese text could not be selected or searched. Exports already converted these fonts; updating the original now does the same, so the rewritten PDF reads back correctly.
