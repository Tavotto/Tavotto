## One name for the pre-submission check, one set of severity words

The check that runs before export is now called the "pre-submission check" everywhere: the export dialog, the problems panel, the canvas inside Codex, error messages, the report written next to exported files, and the guides. It used to appear under several names (preflight, spec check, style-check report). Severity reads the same way in every place: "to fix", "to review", "not verifiable" and "suggestion". In Chinese these are 要改, 需留意, 无法核验 and 建议, replacing the mix of 阻断 / 警告 / 项留意 seen before. What the check looks at and how it decides are unchanged; the report file keeps its `_style-check.json` name.

The check badge on the canvas inside Codex now lists only the counts that are not zero, for example "1 to review" instead of "0 to fix · 1 to review · 0 not verifiable".
