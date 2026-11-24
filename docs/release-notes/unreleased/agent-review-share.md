## Agents can send a figure for external review (asks you first)

The Tavotto plugin for Codex / Claude Code gains `tavotto_share_review`: an agent can turn the figure it has open into an external review link, or add a new version to that figure's existing link. It always asks you first — the first call only shows what would be sent (the picture and element positions, never your script or data) and nothing leaves your computer until you agree. Links made this way show up in the app's Review tab like any other; the daily new-link quota is shared with the app. Turn it off with `TAVOTTO_NO_REVIEW_SHARE=1`.

The agent can now also read the comments reviewers left on that link (grouped by version, with the element each one points at and each reviewer's verdict), reply to a comment (only after you approve the exact reply), and mark comments as resolved. Reviewers' words are treated as to-dos for the agent, never as instructions.
