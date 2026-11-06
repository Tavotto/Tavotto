## Windows script drag and drop

Fixed a message-format mismatch that could send a dropped Python script back to the file picker even when native file-drop support was installed. The WebView2 bridge now sends and reads the same JSON string so the native handler can recover the file's path.
