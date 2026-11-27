ggplot_number_formats <- c("auto", "%.0f", "%.1f", "%.2f", "%.3f", "%g", "sci")

ggplot_number_labels <- function(format, values) {
  sprintf(if (format == "sci") "%.2e" else format, values)
}

ggplot_uniform_breaks <- function(range, step, kind = "major") {
  # Use the same zero-based origin as a standard multiple locator. Limit the
  # generated break count before allocating anything on the rendering path.
  bounds <- range / step
  rounding <- 4 * .Machine$double.eps * abs(bounds)
  first <- ceiling(bounds[[1L]] - rounding[[1L]])
  last <- floor(bounds[[2L]] + rounding[[2L]])
  count <- last - first + 1
  if (!is.finite(count) || count < 1 || count > 100 ||
      any(abs(c(first, last)) >= 2^52)) {
    stop(paste0("The native R ", kind, " tick interval must produce between 1 and 100 visible ticks."))
  }
  seq.int(first, last) * step
}

ggplot_major_tick_mode <- function(edit) {
  if (!is.null(edit$major_mode)) return(edit$major_mode)
  if (!is.null(edit$major_values)) return("fixed")
  if (!is.null(edit$major_step)) return("step")
  "auto"
}

ggplot_fixed_tick_values <- function(value) {
  if (is.list(value) && is.null(names(value)) && all(vapply(value, function(x) {
    is.numeric(x) && length(x) == 1L
  }, logical(1L)))) value <- if (length(value)) unlist(value, use.names = FALSE) else numeric()
  if (!is.numeric(value) || !is.null(dim(value)) || any(!is.finite(value))) {
    stop("The native R fixed ticks must be a list of finite numbers.")
  }
  unname(value)
}

ggplot_usable_fixed_ticks <- function(values, transform) {
  if (transform == "log-10") values <- values[values > 0]
  if (transform == "prob-logis") values <- values[values > 0 & values < 1]
  sort(unique(values))
}

ggplot_restore_tick_locator <- function(scale) {
  original <- scale$tavotto_tick_locator
  ggplot_clone(scale, breaks = original$breaks, labels = original$labels, tavotto_tick_locator = NULL)
}

ggplot_apply_tick_locator <- function(scale, change) {
  saved <- scale$tavotto_tick_locator
  if (!is.null(saved)) scale <- ggplot_restore_tick_locator(scale)
  style <- utils::modifyList(saved$style %||% list(), change)
  original <- list(breaks = scale$breaks, labels = scale$labels, style = style)
  mode <- ggplot_major_tick_mode(style)
  breaks <- if (mode == "step") {
    step <- style$major_step
    force(step)
    function(limits) ggplot_uniform_breaks(limits, step)
  } else if (mode == "fixed" && length(style$major_values)) {
    ggplot_usable_fixed_ticks(ggplot_fixed_tick_values(style$major_values),
                              scale$get_transformation()$name)
  } else scale$breaks
  format <- style$format %||% "auto"
  labels <- if (format == "auto") scale$labels else {
    force(format)
    function(values) ggplot_number_labels(format, values)
  }
  ggplot_clone(scale, breaks = breaks, labels = labels, tavotto_tick_locator = original)
}

ggplot_with_tick_locators <- function(plot, locators) {
  scales <- plot$scales$clone()
  defaults <- list(x = ggplot2::scale_x_continuous, y = ggplot2::scale_y_continuous)
  for (axis in names(locators)) {
    scale <- scales$get_scales(axis)
    if (is.null(scale)) scale <- defaults[[axis]]()
    edited <- ggplot_apply_tick_locator(scale, locators[[axis]])
    # Keep the scale in its authored position without emitting a replacement message.
    indices <- scales$find(axis)
    scales$scales <- if (any(indices)) {
      replace(scales$scales, which(indices), list(edited))
    } else c(scales$scales, list(edited))
  }
  plot$scales <- scales
  plot
}

ggplot_tick_locator_baseline <- function(plot) {
  saved <- list()
  for (axis in c("x", "y")) {
    scale <- plot$scales$get_scales(axis)
    if (!is.null(scale$tavotto_tick_locator)) saved[[axis]] <- scale$tavotto_tick_locator$style
  }
  if (length(saved)) {
    plot$scales <- ggplot_clone(plot$scales, scales = lapply(plot$scales$scales, function(scale) {
      if (is.null(scale$tavotto_tick_locator)) scale else ggplot_restore_tick_locator(scale)
    }))
  }
  list(plot = plot, locators = saved)
}
