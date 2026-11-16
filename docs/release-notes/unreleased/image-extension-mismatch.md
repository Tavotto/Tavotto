## A picture saved under the wrong extension now says so

If a script fails to read an image because its contents don't match its extension (for example a JPEG photo saved as `.png`), the failure card now says exactly that, in one sentence: the file is really a JPEG, and you need a real PNG. The assistant that offers to fix failures gets the same fact, so it no longer suspects the safe run or your environment. Nothing is changed or converted for you: neither your script nor your image file is touched.
