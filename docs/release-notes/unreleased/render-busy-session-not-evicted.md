## Opening several figures at once no longer fails with "render session was reclaimed"

When a project with several figures opened and they all started rendering at the same time on a machine short of memory, one of them could fail with an internal message about the render session being reclaimed. A figure that is still being drawn is no longer pushed out to make room for another one. If a render ever does have to give way, the message now simply says too many figures were being drawn at once and to try again.
