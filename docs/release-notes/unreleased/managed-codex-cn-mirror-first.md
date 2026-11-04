## In-app Codex install no longer waits on the slow official npm source in mainland China

Trigger: one-click installing Codex from inside the app on a machine in
mainland China. Before: the download always started from registry.npmjs.org,
which is around 2 MB/s there — not slow enough to trigger the automatic
switch, so every install waited about an extra minute (174 MB took 85 s
instead of 22 s from npmmirror). Now the desktop app's existing region check
(the same one used for app updates and "Contact the author") puts npmmirror
first; the file and its checksum are identical. If the mirror is slow, it
still switches back to the official source.
