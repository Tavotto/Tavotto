## A broken last project no longer stops the desktop app from starting

If the project Tavotto reopens at launch had a damaged settings or state file, the background service could quit before the window appeared, and "Restart" on the error page led to the same error every time. Tavotto now opens the project picker instead, so you can choose or recreate the project. When the service does quit during startup, the minimal startup report sent from the error page now also records which startup step it reached (the same fixed list already used for startup timeouts; no log text is included).
