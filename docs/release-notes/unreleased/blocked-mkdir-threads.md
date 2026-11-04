## Scripts that create folders from several threads are no longer reported as fully successful after a blocked deletion

In the safe run mode, when Tavotto blocks deleting a folder and the script then creates it again with a plain `mkdir` that collides with the folder left in place, the run is reported as incomplete. If another thread was inside `os.makedirs(..., exist_ok=True)` or `Path.mkdir(exist_ok=True)` at that moment, the collision could be mistaken for that thread's harmless retry and the run reported as a complete success. Each thread's folder creation is now judged on its own.
