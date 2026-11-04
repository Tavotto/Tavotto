## Three hints now match what the app actually does

**The close-window prompt says "Don't save" keeps your local draft.**
Trigger: closing the window with unsaved changes. Before: the prompt only asked
whether to save, so "Don't save" read like throwing the edits away, while the
local autosave copy is kept and offered for recovery on the next start. The
prompt now says so.

**The prepare card marks the text above a prompt-less input as existing output.**
Trigger: a script that reads stdin without a prompt (`sys.stdin.readline()`,
`sys.stdin.read()`) after an earlier `input("Your name? ")`. Before: the card
kept showing `Your name?` above the empty box, so it looked like the same
question asked again. That text is now labelled "Output so far", like in the
input dialog.

**An SVG whose text was converted to paths says so.**
Trigger: exporting a figure to SVG when its source script is unavailable, so the
text falls back to outlines (ADR 0144). Before: the result looked like any
other verified file. The result row now adds one line that the text can no
longer be edited as text in Illustrator and similar tools; it only appears when
the text was actually outlined.
