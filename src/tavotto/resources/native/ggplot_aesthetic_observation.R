# Capture each build's raw scale inputs after stats and positioning.
ggplot_observe_colour_maps <- function(plot, capture) {
  original <- plot$scales
  plot$scales <- ggplot2::ggproto(NULL, original, non_position_scales = function(self, ...) {
    scales <- ggplot2::ggproto_parent(original, self)$non_position_scales(...)
    capture$maps <- list()
    ggplot2::ggproto(NULL, scales, map_df = function(self, df, ...) {
      mapped <- ggplot2::ggproto_parent(scales, self)$map_df(df, ...)
      channels <- intersect(c("colour", "fill", "shape"), names(df))
      row_keys <- intersect(c("PANEL", "group", "x", "y", "xmin", "xmax", "ymin", "ymax"), names(df))
      capture$maps[[length(capture$maps) + 1L]] <- list(
        input = df[channels], output = mapped[channels], rows = df[row_keys]
      )
      mapped
    })
  })
  plot
}

ggplot_aligned_colour_capture <- function(built, maps, index) {
  if (length(maps) != length(built$data)) return(NULL)
  observed <- maps[[index]]
  data <- built$data[[index]]
  if (ggplot_observed_rows_match(observed, data)) observed
}

ggplot_observed_rows_match <- function(observed, data) {
  !is.null(observed) && identical(observed$rows, data[names(observed$rows)])
}

ggplot_observed_categories <- function(capture, index, data, aesthetic) {
  observed <- capture$maps[[index]]
  if (!ggplot_observed_rows_match(observed, data) ||
      !identical(observed$output[[aesthetic]], data[[aesthetic]]) ||
      length(observed$input[[aesthetic]]) != nrow(data)) {
    stop("Tavotto could not match the R layer categories.")
  }
  as.character(observed$input[[aesthetic]])
}
