## Edits an agent made but did not export are no longer lost when the session closes

When an AI agent closed a Tavotto figure session (or it was closed to make room for another) before exporting, the edits it had made were thrown away. Now they are kept as a draft, and opening the same figure again brings them back automatically; edits that no longer fit because the script changed are skipped and listed. Your original script is never touched, and the draft is cleared as soon as the figure is exported. To start from the script as it is, open the figure with `discard_draft`.
