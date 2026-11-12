## OpenCode Go keys connect

Adding an OpenCode Go connection with a valid key no longer fails with "can't connect". The
key check now identifies itself as Tavotto instead of a generic Python client, which OpenCode's
network edge was blocking, and AI fixes that run through the built-in assistant now send the
per-conversation session id OpenCode Go requires.
