## One undo now reverts a resize together with the automatic re-layout

When you resized one picture in a layout group and the others were moved aside automatically, a single undo only moved the others back and left the picture at its new size, so the two overlapped (and could be saved or exported that way). The resize and the re-layout it triggers are now one undo step, and redo restores both.
