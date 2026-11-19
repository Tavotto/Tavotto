## Scripts using pygal world maps can install their dependency in one click

A script that imports `pygal_maps_world` used to get "Tavotto doesn't recognise it", and the package name people typed by hand (`pygal`) installed fine but still could not be imported, because the world maps are a separate package. Tavotto now recognises `pygal` and `pygal_maps_world` and installs the right package.
