## On Windows, dropping a script on the home page opens it directly

On the Windows desktop app, dragging a `.py` file (or an R / MATLAB script, or a folder) onto the home page used to open a file picker and make you find the same script again, because the app could not see where the dropped file was. It now opens the script's folder and runs that script straight away, the same as on macOS; dropping a script onto a canvas in the editor works the same way. Very old WebView2 runtimes (before 1.0.1518, 2023) still fall back to the picker.
