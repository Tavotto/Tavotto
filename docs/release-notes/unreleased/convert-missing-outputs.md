## Conversion no longer passes when a table or log the source writes is dropped

For R and MATLAB sources, Tavotto now checks that the converted script writes the files the source plainly writes (a literal file name in an ordinary top-level statement). If some are missing it asks the AI once to add them. If they are still missing you can still adopt the result, and the missing file names are listed. Dynamic paths cannot be checked and stay marked as unknown.
