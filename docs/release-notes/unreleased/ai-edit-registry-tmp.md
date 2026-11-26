## AI edits no longer fail when Tavotto updates its script registry at the same moment

An AI edit or conversion could occasionally be reported as failed with "project files changed" on slower machines. The only change was Tavotto's own script registry being rewritten in the background. Tavotto's temporary file from that write is now ignored, like the registry itself.
