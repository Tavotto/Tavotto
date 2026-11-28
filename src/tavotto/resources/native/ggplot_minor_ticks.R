ggplot_minor_marks_drawn <- function(elements) {
  inherits(elements$minor, "element_line") && inherits(elements$minor_length, "unit") &&
    any(abs(grid::convertUnit(elements$minor_length, "bigpts", valueOnly = TRUE)) > 0)
}

ggplot_minor_spacing <- function(view) {
  breaks <- view$get_breaks_minor()
  breaks <- sort(unique(breaks[is.finite(breaks)]))
  if (length(breaks) < 2L) return(NULL)
  step <- if (identical(view$scale$get_transformation()$name, "identity")) {
    stats::median(diff(breaks))
  } else ggplot_transformed_locator_step(view, 10)
  list(step = if (is_positive_ggplot_number(step)) step)
}

ggplot_major_axis_key <- function(key) {
  if (".type" %in% names(key)) key[key$.type == "major", , drop = FALSE] else key
}

ggplot_minor_tick_defaults <- function(built, params, panel, axis) {
  defaults <- ggplot_minor_spacing(panel[[axis]])
  if (is.null(defaults)) return(NULL)
  guide <- ggplot2::GuideAxis
  resolved <- guide$setup_params(params)
  elements <- guide$setup_elements(resolved, guide$elements, built$plot$theme)
  line <- ggplot_minor_marks_drawn(elements)
  list(visible = isTRUE(params$minor.ticks) && line &&
         any(params$key$.type == "minor"),
       theme = if (!line) {
         base <- guide$setup_elements(resolved, guide$elements, ggplot2::theme_grey())
         suffix <- paste(axis, params$position, sep = ".")
         replacements <- list(base$minor, base$minor_length)
         names(replacements) <- paste(c("axis.minor.ticks", "axis.minor.ticks.length"), suffix, sep = ".")
         do.call(ggplot2::theme, replacements)
       },
       step = defaults$step)
}

ggplot_edit_minor_ticks <- function(panel, params, spacing, edit, coord) {
  defaults <- spacing$minor
  if (length(edit) && is.null(defaults)) stop("The requested native R minor ticks are unavailable.")
  if (!is.null(edit$minor_visible) && (!is.logical(edit$minor_visible) ||
      length(edit$minor_visible) != 1L || is.na(edit$minor_visible))) {
    stop("Invalid native R minor tick visibility.")
  }
  visible <- edit$minor_visible %||% isTRUE(params$minor.ticks)
  mode <- edit$minor_mode %||% "auto"
  step <- edit$minor_step %||% defaults$step
  allowed <- c("auto", if (!is.null(defaults$step)) "step")
  if (!is.character(mode) || length(mode) != 1L || !mode %in% allowed ||
      (!is.null(edit$minor_step) && !is_positive_ggplot_number(step))) {
    stop("Invalid native R minor tick spacing.")
  }
  axis <- spacing$axis
  view <- panel[[axis]]
  major <- ggplot_major_axis_key(params$key)
  if (mode == "step") {
    trans <- view$scale$get_transformation()
    limits <- sort(trans$inverse(view$continuous_range))
    values <- ggplot_uniform_breaks(limits, step, "minor")
    breaks <- trans$transform(ggplot_usable_fixed_ticks(values, trans$name))
    # Match Matplotlib's tolerance in the projected view. The grid and guide
    # both use this list, even when the minor tick marks are hidden.
    tolerance <- diff(range(view$continuous_range)) * 1e-5
    overlaps <- vapply(breaks, function(value) {
      any(abs(major$.value - value) <= tolerance, na.rm = TRUE)
    }, logical(1L))
    panel[[axis]] <- view <- ggplot_clone(view, minor_breaks = breaks[!overlaps])
  }
  params$minor.ticks <- visible
  params$key <- major
  if (visible) {
    if (isTRUE(edit$minor_visible) && !is.null(defaults$theme)) {
      params$theme <- if (is.null(params$theme)) defaults$theme else params$theme + defaults$theme
    }
    minor <- view$get_breaks_minor()
    minor <- setdiff(minor[is.finite(minor)], major$.value)
    if (length(minor)) {
      key <- data.frame(.value = minor, .label = NA_character_, .type = "minor")
      key[[axis]] <- minor
      current <- list(key = key, position = params$position, aesthetic = axis)
      major$.type <- rep("major", nrow(major))
      params$key <- rbind(major, ggplot2::GuideAxis$transform(current, coord, panel)$key)
    }
  }
  list(panel = panel, params = params)
}

ggplot_format_minor_labels <- function(panel, params, format) {
  if (is.null(format)) return(params)
  if (!is.character(format) || length(format) != 1L || !format %in% c("none", ggplot_number_formats)) {
    stop("Invalid native R minor tick number format.")
  }
  if (format == "none" || !isTRUE(params$minor.ticks) || !length(params$key$.type)) {
    return(params)
  }
  minor <- params$key$.type == "minor"
  if (!any(minor)) return(params)
  scale <- panel[[params$aesthetic]]$scale
  breaks <- params$key$.value[minor]
  labels <- if (format == "auto") scale$get_labels(breaks) else {
    ggplot_number_labels(format, scale$get_transformation()$inverse(breaks))
  }
  params$key$.label[minor] <- as.character(labels)
  params$tavotto_minor_labels <- TRUE
  params
}

ggplot_guide_with_minor_labels <- function(guide, params) {
  if (!isTRUE(params$tavotto_minor_labels)) return(guide)
  if (inherits(guide, "TavottoMinorLabelAxis")) return(guide)
  ggplot2::ggproto("TavottoMinorLabelAxis", guide, build_labels = function(key, elements, params) {
    # Draw the same guide, with all of its labels. The native tick builder
    # continues to see the original key and uses separate styles per tier.
    key$.type <- NULL
    guide$build_labels(key, elements, params)
  })
}
