## Data to Figure keeps percentage units and declared error types

- A numeric text column recognized as percentages now keeps `%` on the figure. A value of `12.5%` stays at 12.5 on a percentage axis, while an unlabelled decimal such as `0.125` stays a decimal.
- Error bars read from a column marked SD, SEM, or CI retain that declared type. The selector can identify an unlabeled error column or recalculate error bars from raw measurements, but it does not relabel an existing SD column as SEM.
