## Figures whose script names elements in Chinese now export

If your script gave plot elements names with Chinese characters or spaces (for example
`ax.set_gid("面板 A")`), exporting the figure from the canvas could fail for every format.
Tavotto now renames those ids inside the exported SVG and keeps every reference pointing at
the same element. Your script is not changed, and figures without such names export exactly as before.
