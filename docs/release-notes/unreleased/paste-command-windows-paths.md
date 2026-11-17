## Pasting a command with Windows paths keeps the backslashes

When you paste a command such as `python plot.py --input F:\data\t1\a.xlsx` into the script arguments box on Windows,
the backslashes in the path are now kept as they are instead of being dropped (which turned the path into
`F:datat1a.xlsx` and made the script report that it could not find the file). Quoted paths with spaces, and
`"C:\dir\\"` style trailing backslashes, work too. macOS and Linux are unchanged.
