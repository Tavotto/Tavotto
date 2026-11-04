## AI editors get up-to-date Tavotto instructions

**Existing Pillow / plotly / R / MATLAB scripts are no longer refused.**
Trigger: asking Codex, Claude Code, Cursor or another connected AI editor to
make an existing non-Matplotlib figure editable. Before: the Tavotto skill
said "use Matplotlib only", so the agent asked you to rewrite the script or
said it was unsupported. Now it leaves the original script untouched and
writes a new `<name>_matplotlib.py` next to it — the same rule as the
desktop app's "Generate editable version".

**One-click connect and Antigravity are mentioned.** When Tavotto tools are
missing in an AI editor, the agent now points desktop users to Settings ›
Figure assistants › Connect AI editors first (including Google Antigravity), and
only falls back to terminal commands when that is not available.

**The skill knows the newer desktop features.** Rotated / vertical canvas
text, copying figure text as annotations, edge drag to pad or crop, the
version timeline, and two-colour gradient peaks are now listed, so the agent
hands these off to the desktop window instead of saying they can't be done.
