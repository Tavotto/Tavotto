## Refresh figures after changing the script working directory

When a script successfully drew an empty curve because a relative file search found no data, choosing the correct working directory could leave the old empty figure on the canvas. Changing the working directory now invalidates figures and captured results from the previous directory, discards late render responses, and lets the canvas rebuild with the data. A PNG waiting for the directory choice continues through its existing source validation.
