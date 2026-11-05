## AI editors explain why a journal's guidelines couldn't be fetched

Trigger: asking a connected AI editor to check a figure against a journal whose
guidelines haven't been downloaded to this computer yet.
Before: the agent's instructions didn't cover the new download step, so when a
download failed it had no guidance on what to tell you.
Now it says plainly what happened: the download limit is used up (try again in
about N minutes), you need to be online, or the data failed verification and
was discarded. It doesn't retry around the limit or fill in numbers from
memory.
