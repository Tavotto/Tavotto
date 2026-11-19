## AI agents are told why a one-step journal fix could not finish

When `tavotto_fix_to_journal` has to undo itself, it now says which step got stuck and which elements were in the way, for example an inset that would be pushed out of the figure at the new width. For the figure-size step it also tells the agent to change `figsize` in the script, not to force the size with overrides.
