## Converting scripts with the official OpenAI models in Codex now works on Windows

On Windows, "Make editable version" with Codex signed in to an official OpenAI model could read your script and data and then stop with "the AI was not allowed to write the new file", because Codex's own file-writing tool fails inside its Windows sandbox. Tavotto now writes the new Matplotlib script itself on Codex's behalf (only that one file, inside Tavotto's own working folder), so the conversion goes through. Your original script and project folder are never touched.
