## Border and tick widths no longer stop at a small limit on scaled figures

The maximum for border width, tick width, tick length and grid width was fixed in script units, so a figure scaled to 45% on the canvas could not go past 1.36 pt for its border. These fields now go up to 10 pt (tick length 20 pt) before scaling, matching the range in style settings.
