## Chinese in serif canvas text now exports in a serif (Song) face

Text labels on the canvas default to a serif font, and the Chinese in them already shows in a Song-style face on the canvas. The export used to draw that Chinese in a sans-serif (Hei-style) face, so the exported figure did not match the canvas. Chinese in serif labels is now exported with Noto Serif SC, matching what you see; sans-serif and monospace labels keep Noto Sans SC. This adds one font file to the installer, making it roughly 10 MB larger.
