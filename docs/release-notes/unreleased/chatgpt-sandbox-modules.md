## Scripts written by ChatGPT that import `artifact_tool` no longer fail to install dependencies

Scripts generated in ChatGPT sometimes import `artifact_tool`, `ace_tools` or `caas_jupyter_tools`. These modules only
exist inside ChatGPT's web sandbox, so installing dependencies failed for the whole batch with "the package isn't in
the index". Tavotto now installs the other packages, explains in one sentence that the module only exists in ChatGPT,
and offers a button that copies a note asking the AI to regenerate the script without it. Your script is not changed.
