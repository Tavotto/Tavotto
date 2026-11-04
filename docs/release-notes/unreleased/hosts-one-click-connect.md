## Connecting an AI editor no longer asks for a folder

**One click connects.** Trigger: Settings → "Connect AI editors" → "Connect"
on any editor. Before: the first connection opened a folder picker, and
every editor was then limited to that one folder — in Claude Code, VS Code or
WorkBuddy, opening a figure from another project failed with "path out of
scope". Now there is no picker and no "Change folder" item:

- Claude Code, VS Code and WorkBuddy use the project the editor itself is
  working in, so switching projects just works.
- Editors that do not report a project (Claude desktop, TRAE, Cursor, ZCode,
  Antigravity) can open figures anywhere on this computer — any drive, any
  folder. Only credential files stay blocked: `.env` files, private keys,
  SSH/cloud credentials, browser login data and Tavotto's own key file (the
  same list the fix assistant uses).

Editors connected with an earlier version are updated automatically the next
time Settings is opened (the old config is backed up first). Start a new
chat in the editor afterwards. `tavotto hosts connect <editor>
--project-root <folder>` still limits an editor to one folder.
