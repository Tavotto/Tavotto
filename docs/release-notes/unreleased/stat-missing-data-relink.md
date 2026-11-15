## Scripts that check a data file's size or date before reading it can be relinked

When a script builds a data path in code and checks it before reading (for example `path.stat()`, `os.path.getsize`, `os.path.getmtime` or `os.listdir` on a folder), and that file or folder is gone, Tavotto now reports the data as missing instead of a generic script error. You can point it at the right file once, and the AI repair assistant can link the file you chose. Before this fix, the link was refused with "not a path this script was found to be missing".
