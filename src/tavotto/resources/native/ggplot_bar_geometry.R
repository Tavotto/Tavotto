# Draw-time bar geometry is shared by the editor and generated R source.
# Stat and position calculations have already produced the rows and centres.
ggplot_project_bar_ends <- function(span, panel, coord, lower, upper) {
  ends <- stats::setNames(data.frame(lower, upper), span$ends)
  projected <- coord$transform(ends, panel)
  list(origin = as.numeric(projected[[span$origin_key]]),
       dimension = as.numeric(projected[[span$ends[[2L]]]] - projected[[span$ends[[1L]]]]))
}

ggplot_bar_width_dimensions <- function(span, width, panel, coord) {
  lower <- span$centre - width / 2
  upper <- span$centre + width / 2
  if (!isTRUE(panel[[span$axis]]$scale_is_discrete)) {
    transform <- panel[[span$axis]]$scale$get_transformation()
    if (identical(transform$name, "log-10") && any(lower <= 0)) {
      stop("The native R bar width would cross zero on a logarithmic axis.")
    }
    lower <- transform$transform(lower)
    upper <- transform$transform(upper)
  }
  if (any(!is.finite(lower) | !is.finite(upper) | upper <= lower)) {
    stop("The native R bar width cannot be represented at the captured positions.")
  }
  dims <- ggplot_project_bar_ends(span, panel, coord, lower, upper)
  if (any(!is.finite(dims$origin) | !is.finite(dims$dimension) | dims$dimension == 0)) {
    stop("The native R bar width cannot be represented in the current view.")
  }
  dims
}

ggplot_bar_span <- function(layout, panel_id, data, rect, transforms = NULL) {
  flipped <- unique(data$flipped_aes)
  if (length(flipped) != 1L || is.na(flipped) ||
      !identical(as.list(layout$coord)$transform, as.list(ggplot2::CoordCartesian)$transform)) {
    return(NULL)
  }
  axis <- if (flipped) "y" else "x"
  ends <- paste0(axis, c("min", "max"))
  if (!all(ends %in% names(data))) return(NULL)
  panel <- layout$panel_params[[panel_id]]
  coord <- layout$coord
  lower <- as.numeric(data[[ends[[1L]]]])
  upper <- as.numeric(data[[ends[[2L]]]])
  span <- list(axis = axis, ends = ends, origin_key = ends[[if (flipped) 2L else 1L]],
               dimension = if (flipped) "height" else "width",
               panel = panel, coord = coord,
               prop = if (flipped) "bar_thickness" else "bar_width")
  original <- ggplot_project_bar_ends(span, panel, coord, lower, upper)
  if (!isTRUE(panel[[axis]]$scale_is_discrete)) {
    transform <- panel[[axis]]$scale$get_transformation()
    if (!is.null(transforms) && !transform$name %in% transforms) return(NULL)
    lower <- transform$inverse(lower)
    upper <- transform$inverse(upper)
  }
  width <- upper - lower
  centre <- lower + width / 2
  if (any(!is.finite(width) | width <= 0 | !is.finite(centre))) return(NULL)
  span$centre <- centre
  authored <- attr(rect, "tavotto_bar_width", exact = TRUE)
  if (!is.null(authored)) {
    if (!is.numeric(authored) || length(authored) != 1L || !is.finite(authored) || authored <= 0) return(NULL)
    original <- ggplot_bar_width_dimensions(span, authored, panel, coord)
    width <- rep_len(authored, length(centre))
  }
  if (!isTRUE(all.equal(original$origin, as.numeric(rect[[axis]]), tolerance = 1e-12)) ||
      !isTRUE(all.equal(original$dimension, as.numeric(rect[[span$dimension]]), tolerance = 1e-12))) return(NULL)
  span$width <- width[[1L]]
  span$uniform <- all(abs(width - width[[1L]]) <= sqrt(.Machine$double.eps) * width[[1L]])
  span
}

ggplot_edit_bar_span <- function(rect, span, width, panel, coord) {
  dims <- ggplot_bar_width_dimensions(span, width, panel, coord)
  rows <- if (is.null(span$rows)) seq_along(dims$origin) else span$rows
  rect[[span$axis]][rows] <- grid::unit(dims$origin, "native")
  rect[[span$dimension]][rows] <- grid::unit(dims$dimension, "native")
  rect
}

ggplot_apply_bar_width <- function(rect, rows, layout, panel_id, values) {
  span <- ggplot_bar_span(layout, panel_id, rows, rect)
  if (is.null(span)) stop("Tavotto could not match the original R bar geometry.")
  width <- values[[span$prop]]
  if (is.null(width)) stop("Tavotto could not match the original R bar direction.")
  rect <- ggplot_edit_bar_span(rect, span, width, span$panel, span$coord)
  attr(rect, "tavotto_bar_width") <- width
  rect
}
