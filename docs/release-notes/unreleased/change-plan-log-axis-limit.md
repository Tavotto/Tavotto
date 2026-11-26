## Change plans now ask before turning an axis into a log scale with a lower limit of 0

When the Agent turns an axis into a log scale and the axis currently starts at 0 or below, the figure used to collapse and the plan still offered "Apply". The plan card now asks one question first ("A log axis can't start at or below 0. Also change the y lower limit to 10⁻²?"). "Change both" gives you the corrected figure; "Not now" drops that change and tells you it wasn't applied.

A request that is partly a figure edit and partly a script change (for example "change the x unit to seconds and convert the data") is no longer split in half: the whole request goes to the script, so you never get a label that says seconds over data that is still in minutes. Change-plan cards also read correctly for a single change in English ("1 place", "1 change").

Powers of ten on a log axis are written as 10⁻², 10⁰, 10³ rather than 0.01, 1, 1000. And when a change makes the tick labels wider (0.8 becomes 10⁻¹) and pushes something such as the y-axis title past the edge of the figure, the Agent now makes room itself by nudging the margins, as part of the same change: if you skip that change, the margins are left alone too.

When a reviewer's comment can only be done by editing the script, the plan card now offers "Edit the script for the rest" (before, only plans made in the assistant did). It never starts on its own: when you press it, the Agent drafts a script plan from those comments, treating the reviewer's words as the thing to change rather than as instructions, and you review the result before anything changes.
