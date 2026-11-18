## AI repair can now add a small launcher instead of giving up

Some scripts are fine but never produce a figure when run on their own: their drawing functions are only defined, or they need data loaded or a setting made first. Repairing with your AI used to stop at "could not fix" for these. The AI can now write a few lines of glue that call your own code, saved in Tavotto's folder inside the project. Tavotto then runs your script through it, and the figure appears in the same panel as before. Your script and your code folders are not changed, and the launcher can be undone.
