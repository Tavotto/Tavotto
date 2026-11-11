## Scripts that look for their data next to themselves now draw on the first try

A script that finds its data with a relative path, for example `glob("data/*.csv")` or `pd.read_parquet("data.parquet")`, used to come back with no figure, an empty plot, or an error such as "x and y must have same first dimension". You then had to click "Run in the script folder". Now, if the data turns out to be in the script's folder, Tavotto switches the project to run there and runs the script once more, without asking. You get the figure straight away.

- This happens only in projects where you haven't picked a working folder yet. If you chose "Sandbox" in Settings, Tavotto leaves it alone and shows the button as before.
- You can switch back to Sandbox in Settings at any time. Files are protected exactly as when you click the button yourself.
- The script runs twice that first time. After that, it runs only once.
