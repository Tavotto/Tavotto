# The desktop renderer and exported source use the same sampled colour mapping.
ggplot_palette <- function(packed) {
  starts <- seq.int(1L, nchar(packed), by = 6L)
  colors <- paste0("#", substring(packed, starts, starts + 5L))
  size <- length(colors)
  function(x) {
    index <- pmax(1, pmin(floor(x * size) + 1, size))
    index[!is.finite(x)] <- NA_integer_
    colors[index]
  }
}

ggplot_set_colour_mapping <- function(scale, limits, packed) {
  if (!is.null(limits)) {
    scale$limits <- scale$get_transformation()$transform(limits)
    scale$breaks <- ggplot2::waiver()
    # Extrema use endpoint colors, while missing values keep the source color.
    scale$oob <- scales::squish
  }
  if (!is.null(packed)) scale$palette <- ggplot_palette(packed)
  scale
}
