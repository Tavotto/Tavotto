## "Draw from data" counts every row it leaves out, and no longer plots an error column as data

In the experimental "draw from data" page (still off by default): rows that cannot be drawn because their group, panel or category cell is empty, or because a point has no Y value, are now counted along with rows whose X is not a value. A single empty group cell no longer makes a whole trend or box plot unavailable. Columns whose names say they hold an error (such as `sd`, `sem` or `mass_sd`) are no longer picked as a measured series or as the X axis unless you choose them yourself.
