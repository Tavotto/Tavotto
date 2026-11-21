## AI repair can test its guesses before changing anything

When a script fails, the repair assistant can now run a few lines of Python in your project's own environment, such as checking that an import works, what arguments the script expects, or what the first rows of a data file look like, and see the answer within seconds. It then picks a fix based on what it found instead of guessing. These experiments cannot change anything: nothing is written to your project, and they cannot use the network or read credential folders such as `~/.ssh`.
