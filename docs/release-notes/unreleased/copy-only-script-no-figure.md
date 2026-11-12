## Clearer message for scripts that only copy existing images

Some scripts don't draw anything: they just copy ready-made PDF or PNG files into place, for example a submission package that re-exports finished figures. When such a script finished, Tavotto used to say it produced no figure, guess that a data file was missing, and offer the AI fixer, which had nothing to fix. Tavotto now recognizes this case and says the script only wrote out image files. The button opens the output images in the asset library, and the AI fixer isn't offered.
