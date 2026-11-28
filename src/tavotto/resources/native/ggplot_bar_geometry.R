# Draw-time bar geometry is shared by the editor and generated R source.
# Stat and position calculations have already produced the rows and centres.
ggplot_project_bar_ends <- function(span, panel, coord, lower, upper) {
  ends <- stats::setNames(data.frame(lower, upper), span$ends)
  projected <- coord$transform(ends, panel)
  list(origin = as.numeric(projected[[span$origin_key]]),
       dimension = as.numeric(projected[[span$ends[[2L]]]] - projected[[span$ends[[1L]]]]))
}

ggplot_bar_width_dimensions <- function(span, width, panel, coord) {
  if (any(!is.finite(width) | width <= 0)) {
    stop("The native R bar width cannot be represented at the captured positions.")
  }
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
    if (!is.numeric(authored) || !length(authored) %in% c(1L, length(centre)) ||
        any(!is.na(authored) & (!is.finite(authored) | authored <= 0))) return(NULL)
    authored <- rep_len(authored, length(centre))
    width[!is.na(authored)] <- authored[!is.na(authored)]
    original <- ggplot_bar_width_dimensions(span, width, panel, coord)
  }
  if (!isTRUE(all.equal(original$origin, as.numeric(rect[[axis]]), tolerance = 1e-12)) ||
      !isTRUE(all.equal(original$dimension, as.numeric(rect[[span$dimension]]), tolerance = 1e-12))) return(NULL)
  span$widths <- width
  span$width <- width[[1L]]
  span$uniform <- !is.null(ggplot_common_bar_width(width))
  span
}

ggplot_common_bar_width <- function(widths) {
  first <- widths[[1L]]
  if (all(abs(widths - first) <= sqrt(.Machine$double.eps) * first)) first
}

ggplot_edit_bar_span <- function(rect, span, width, panel, coord) {
  dims <- ggplot_bar_width_dimensions(span, width, panel, coord)
  rows <- if (is.null(span$rows)) seq_along(dims$origin) else span$rows
  rect[[span$axis]][rows] <- grid::unit(dims$origin, "native")
  rect[[span$dimension]][rows] <- grid::unit(dims$dimension, "native")
  rect
}

ggplot_edit_bar_alpha <- function(rect, rows, alpha) {
  for (field in c("fill", "col")) {
    colour <- rep_len(rect$gp[[field]], length(rect$x))
    colour[rows] <- scales::alpha(colour[rows], alpha)
    rect$gp[[field]] <- colour
  }
  attr(rect, "tavotto_bar_alpha") <- TRUE
  rect
}

ggplot_edit_bar_fill <- function(rect, rows, value) {
  colours <- rep_len(rect$gp$fill, length(rect$x))
  original <- colours[rows]
  opacity <- grDevices::col2rgb(original, alpha = TRUE)[4L, ] / 255
  missing <- is.na(original)
  if (any(missing)) {
    borders <- rep_len(rect$gp$col, length(rect$x))[rows][missing]
    fallback <- grDevices::col2rgb(borders, alpha = TRUE)[4L, ] / 255
    opacity[missing] <- ifelse(fallback > 0, fallback, 1)
  }
  value[value == "none"] <- NA_character_
  colours[rows] <- scales::alpha(value, opacity)
  rect$gp$fill <- colours
  attr(rect, "tavotto_bar_alpha") <- TRUE
  rect
}

ggplot_apply_bar_geometry <- function(rect, rows, layout, panel_id, values, hatches = NULL) {
  rect <- ggplot_bar_native_rect(rect)
  span <- ggplot_bar_span(layout, panel_id, rows, rect)
  if (is.null(span)) stop("Tavotto could not match the original R bar geometry.")
  if (!is.null(values$facecolor)) {
    selected <- which(!is.na(values$facecolor))
    rect <- ggplot_edit_bar_fill(rect, selected, values$facecolor[selected])
  }
  if (!is.null(values$alpha)) {
    selected <- which(!is.na(values$alpha))
    rect <- ggplot_edit_bar_alpha(rect, selected, values$alpha[selected])
  }
  if (any(names(values) %in% ggplot_bar_hatch_props) ||
      (!is.null(values$alpha) && !is.null(attr(rect, "tavotto_bar_hatching", exact = TRUE)))) {
    rect <- ggplot_record_bar_hatch(rect, seq_len(nrow(rows)), values, rows, hatches)
  }
  width <- values[[span$prop]]
  if (is.null(width)) {
    if (length(setdiff(names(values), c("alpha", "facecolor", ggplot_bar_hatch_props)))) stop("Tavotto could not match the original R bar direction.")
    return(ggplot_draw_bar_hatching(rect))
  }
  width <- rep_len(width, length(span$centre))
  selected <- which(!is.na(width))
  original <- span$widths
  original[selected] <- width[selected]
  span$centre <- span$centre[selected]
  span$rows <- selected
  rect <- ggplot_edit_bar_span(rect, span, width[selected], span$panel, span$coord)
  attr(rect, "tavotto_bar_width") <- original
  ggplot_draw_bar_hatching(rect)
}
