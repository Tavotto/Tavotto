## Agents no longer need to close and reopen a figure after editing its script

When an AI agent using Tavotto over MCP edits the plotting script, its next call (open, preflight, export and so on) now reruns the same session with the new script and keeps the edits it already made; any edit that no longer fits the new script is skipped and listed. Previously the session kept running the old script, so agents had to close and reopen the figure — more than four opens per figure on average in our evaluation.
