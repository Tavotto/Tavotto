## Undoing tick-label visibility or arrow color no longer leaves the figure in a different state

On a colorbar or a twin axis, hiding the tick labels did nothing, because those labels sit on the right side. Undoing that change then turned on an extra row of labels on the left, and that row stayed in the editor after later tick edits but was gone after a fresh run. Hiding tick labels now hides them on both sides, and undo puts back exactly the sides the script showed.

Undoing a color change on an arrow whose fill differs from its edge used to repaint the fill in the edge color. Undo now restores the edge color and the fill separately.
