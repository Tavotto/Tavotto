## AI editors now tell you when Tavotto is out of date

The update reminder only reached the AI on the desktop hand-off path, so sessions using the MCP tools never mentioned a new version. `tavotto_health` and a successful `tavotto_export` now carry the notice, and the AI passes it on once at the end of the task. The check runs in the background and never slows a tool call. Any of the existing update-check switches turns it off.
