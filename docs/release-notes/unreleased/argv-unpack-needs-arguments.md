## Scripts that unpack their command-line arguments now ask for them instead of failing

A script that takes its arguments in one line, such as `data, work, out = sys.argv[1:4]`, stopped with "not enough values to unpack (expected 3, got 0)" when it was run without arguments, and Tavotto offered an AI fix that could not supply them. Tavotto now recognises this as a script that needs run arguments and opens the existing run-arguments entry, the same as for scripts that read `sys.argv[1]` directly. Other unpacking errors are reported as before.
