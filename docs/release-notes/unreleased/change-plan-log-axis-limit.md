## Change plans now ask before turning an axis into a log scale with a lower limit of 0

When the Agent turns an axis into a log scale and the axis currently starts at 0 or below, the figure used to collapse and the plan still offered "Apply". The plan card now asks one question first ("A log axis needs a lower limit above 0. Also change the y lower limit to 0.01?"). "Change both" gives you the corrected figure; "Leave it" drops that change and tells you it wasn't applied.

A request that is partly a figure edit and partly a script change (for example "change the x unit to seconds and convert the data") is no longer split in half: the whole request goes to the script, so you never get a label that says seconds over data that is still in minutes. Change-plan cards also read correctly for a single change in English ("1 place", "1 change").
