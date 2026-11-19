## Opening Tavotto from the Codex desktop app on Windows works again

On some Windows machines, asking Codex to open Tavotto failed with an error about a missing `ai_catalog.json`, even though Tavotto opened fine on its own. This happened when Tavotto had once been installed or updated from inside Codex, which leaves an incomplete copy in Codex's private folder. Tavotto now notices when it is started from such a copy and switches to your real installation instead.
