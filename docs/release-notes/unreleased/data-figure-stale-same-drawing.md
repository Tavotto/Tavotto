## Creating a figure after the data changed no longer swaps in a different figure

If the options went stale when you clicked "Create editable figure" (the session expired, or the data file changed), Tavotto refreshes them and continues only when the refreshed option draws the same figure: same columns, grouping, layers and error bars. If the data change made the same kind of chart use different columns or grouping, it now stops on the refreshed preview so you can look before creating it, instead of silently creating a figure you have not seen.
