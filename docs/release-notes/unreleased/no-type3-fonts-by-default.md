## Figure scripts written by the agent and the tutorial no longer embed Type 3 fonts

Original PDFs saved by figure scripts used matplotlib's default Type 3 fonts, which journals such as IEEE and Elsevier reject and which other programs often can't select or edit. The figure template the Codex agent follows now sets `pdf.fonttype` to 42 (TrueType), and the tutorial project's style and its two sample figures were regenerated the same way. Tavotto's own exports were already TrueType; scripts you wrote yourself are unchanged.
