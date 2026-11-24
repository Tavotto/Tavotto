## Fix to journal: colorbar tick labels are fixed too

"Fix to journal" no longer gives up on figures with a colorbar whose tick labels are below the journal's minimum font size. Previously the whole fix was rolled back and the colorbar ticks stayed too small; now they are raised like the other tick labels, and the rest of the fix lands as before.
