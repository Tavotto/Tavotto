## Conversion checks the tables and logs the source writes

For R and MATLAB sources, Tavotto now checks that the converted script writes the files the source plainly writes (a literal file name in an ordinary top-level statement). If some are missing it asks the AI once to add them; if they are still missing you can point at a file of your own or skip them, and still adopt the result.

When the project already holds an earlier result for a file, the new one is compared with it (CSV, TSV and XLSX cell by cell, text and log files line by line) and marked same, different or can't compare, with the reason. If something differs you can ask the AI to check once more. Dynamic paths and unsupported formats cannot be checked and stay marked as such.
