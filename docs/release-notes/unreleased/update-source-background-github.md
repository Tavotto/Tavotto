## Update downloads in mainland China no longer lean on the mirror by default

Trigger: the desktop app on a machine in mainland China (time zone or
Simplified Chinese language) finds a new version. Before: the check and the
download both used the mirror at `dl.tavotto.cn`, so every download in the
background went through the mirror. Now the check is unchanged (the mirror
still answers first, which is fast and small), but the installer is
downloaded from GitHub first: the background download goes to GitHub and does
not switch just because it is slow; it moves to the mirror only after it has
failed about 3 times in a row, or when the new version has been out for more
than about 3 days and is still not downloaded. When you press Download and
install yourself, GitHub gets about 8 seconds; if it has sent nothing by then,
or is clearly going to need more than about 2 minutes, the download moves to
the mirror. It is always the same version you were shown, and the installer is
verified with the same built-in key whichever source it came from. Outside
mainland China nothing changes. This applies from the first version that
contains it onward: an already installed older version keeps its old behavior
when updating to it.
