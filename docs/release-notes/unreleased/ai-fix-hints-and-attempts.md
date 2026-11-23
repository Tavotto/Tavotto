## AI repair learns from each failed try

When a run fails, the repair assistant now gets a short, specific suggestion for that kind of failure, such as a missing environment variable, a relative import, or a script waiting for typed input. After every trial run it also sees what it already tried and whether the error changed, and it is told plainly when the same failure has come back twice so it stops repeating itself. If the real entry point is a different script, it tells you which one to open instead of running it for you.

When you send a problem report after using AI repair, the report now includes what the assistant tried, the parts of your script it read, and the launcher it wrote, with keys and your home folder removed. The send dialog says so.
