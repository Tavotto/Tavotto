## Scripts that write a temporary file and replace it no longer fall back

A script that writes a file inside the project (for example a temporary file that it then
renames over an existing cache) and reads the result back now runs in safe mode and draws its
figure, instead of stopping with "safe mode did not really perform a file operation" and
retrying natively. The changes live in a per-run scratch layer that is discarded when the run
ends, so your project files stay byte-for-byte unchanged. Scripts that inspect those paths in
ways the scratch layer cannot reproduce (for example `os.stat`) are still reported and run
natively, as before.
