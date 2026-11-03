## Script stdin size and hint arguments now behave like a real text stream

When a script reads from standard input in Tavotto, `readline(size)` and `read(size)` now return at most `size` characters and keep the rest for the next read or `input()`, instead of handing over the whole line and asking again. `readlines(hint)` stops only once the total length exceeds `hint`, as Python does (both while preparing a script and when running it in the desktop app).
