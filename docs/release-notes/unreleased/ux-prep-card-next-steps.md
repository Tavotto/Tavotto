## Failed script cards now always offer a way forward

When a script couldn't be opened or run, the card could end with a button that could not possibly work, or with a raw error. Now every failure ends one of three ways: it is fixed quietly, it gives one sentence and one button that does what you were trying to do, or it offers "Send it to us".

- If your AI isn't connected, the main button is now "Send it to us"; "Fix it with your AI" is still there as the second button.
- "Can't run yet" now says why, and no longer offers a "Check again" that would fail again.
- A script that finishes without a figure because it couldn't find its data now lets you point to the data, instead of only saying nothing was drawn.
- For figure-name conflicts, the main button stays "Open Connections", and the AI fixer is no longer offered where it can't help.
- Folder permission problems, scripts that call `sys.exit()`, and rendering that was interrupted now say what happened in plain words instead of showing a raw error or process details.
