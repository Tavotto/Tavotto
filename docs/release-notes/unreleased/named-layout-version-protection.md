## Protect named layouts from unsupported versions

When a named layout was created by a newer version or contained an unreadable structure, opening it could produce an empty layout and a later save could overwrite the original. Tavotto now refuses the known unsafe inputs before binding the file. The original file remains unchanged; valid supported layouts can still be opened.
