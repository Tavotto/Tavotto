## SVG export keeps working after many edits to a large figure

Every edit of a script figure saves an export-grade copy inside the project so SVG export can reuse it. Those copies were never removed, so after a few dozen edits of a large figure the project's copy store filled up and SVG export of that project kept asking you to run the script again, even after you did. When the store is full, Tavotto now removes old copies that no saved layout, version or autosave refers to, and keeps going. Copies your saved layouts use are never removed.
