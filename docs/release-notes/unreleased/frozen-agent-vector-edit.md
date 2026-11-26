## Fixed: in the desktop app, agents can edit existing vector objects again

When an AI editor was connected to the desktop app, asking the agent to change arrows, shapes or paths already on a layout failed with "this version can't edit vector objects yet", because one part of the engine was missing from the installed app. It is now included, and every build checks that it is there and that the geometry engine loads.
