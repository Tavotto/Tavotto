## Journal checks flag data lines that are too thin

For journals whose guideline states a minimum line width with no conditions (for example "at least 2 pt"), Tavotto now flags data lines thinner than that, measured at the final size on the page. Only data lines are checked (curves, error bars, bars with a visible outline); axes, ticks, grid lines and legend frames are not. Projects already set to such a journal pick this up when you open them, so you may see new line-width notes. Requirements that depend on a condition stay in the collapsed "Needs your check" group.
