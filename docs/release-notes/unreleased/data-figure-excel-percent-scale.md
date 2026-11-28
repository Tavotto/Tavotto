## Excel and CSV percentages share the same scale in Data to Figure

A number displayed as `12.5%` by an actual Excel percentage format is plotted as 12.5 on a percent axis, the same as `12.5%` in a CSV. Plain numbers and a quoted percent sign are not treated as a percentage format. If a shared column mixes known percentages with plain numbers, Tavotto stops rather than guessing the scale.
