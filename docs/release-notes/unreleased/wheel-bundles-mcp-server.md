## `tavotto mcp` now works from a pip install

If you installed Tavotto with pip, pipx or uvx, `tavotto mcp` now starts the MCP server directly. Before,
it only worked from the desktop app or the plugin. On a headless machine you can run
`uvx --from "tavotto[worker]" tavotto mcp`. Nothing changes if you never run that command.
