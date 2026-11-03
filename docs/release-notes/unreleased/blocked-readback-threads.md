## Scripts that check for files from several threads are no longer reported as fully successful after a blocked file operation

In the safe run mode, when Tavotto blocks a file operation (for example deleting or replacing a data file) and the script then checks for that file, the run is reported as incomplete. If the script — or a library it uses — checked for files from more than one thread at the same time, some of those checks were missed and the run could be reported as a complete success. Every thread's checks are now counted.
