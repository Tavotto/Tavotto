## Recolouring a gradient peak also recolours its legend swatch

When a script draws its own legend as small gradient swatches next to the labels, changing a gradient peak's colours now recolours the matching legend swatch in the same subplot as well, so the legend keeps agreeing with the figure. Tavotto pairs a peak with the swatch whose colours match it; when several swatches have the same colours it does not guess, and the properties panel lists them so you can pick the one to recolour. Undo or Reset returns both the peak and the swatch to the script's colours.
