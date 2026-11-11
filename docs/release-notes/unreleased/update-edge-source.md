## Background updates in mainland China can use one more download server

When Tavotto downloads an update in the background and GitHub is unreachable, it now tries a second download server in mainland China before falling back to the mirror. The update is still verified with the signature built into the app, so a different server never changes what is trusted. Checking for updates and downloads you start yourself behave as before; users outside mainland China are not affected.
