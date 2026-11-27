# Reproject the captured positions in original data units. The source's
# stat, group, and position results stay the inputs to the stock geoms.
ggplot_axes_gid <- "axes_0"
ggplot_axis_modes <- c(identity = "linear", "log-10" = "log",
                       "tavotto-symlog" = "symlog", "prob-logis" = "logit")
ggplot_axis_names <- c("x", "y")

ggplot_transform_symlog <- function() {
  # Match the Matplotlib symlog preset used by the shared scale control.
  base <- 10
  threshold <- 2
  slope <- 1 / (1 - 1 / base)
  scales::new_transform("tavotto-symlog", transform = function(values) {
    result <- values * slope
    outside <- which(abs(values) > threshold)
    tail <- values[outside]
    result[outside] <- sign(tail) * threshold * (slope + log(abs(tail) / threshold, base))
    result
  }, inverse = function(values) {
    result <- values / slope
    outside <- which(abs(values) > threshold * slope)
    tail <- values[outside]
    result[outside] <- sign(tail) * threshold * base^(abs(tail) / threshold - slope)
    result
  })
}

ggplot_inverted <- function(reverse, axis) isTRUE(grepl(axis, reverse, fixed = TRUE))

ggplot_axis_position <- function(panel, axis, values) {
  view <- panel[[axis]]
  if (ggplot_inverted(panel$reverse, axis)) view$reverse(values) else view$rescale(values)
}

ggplot_axis_pair <- function(value) {
  if (is.list(value) && length(value) == 2L && all(vapply(value, function(end) {
    is.numeric(end) && length(end) == 1L && is.finite(end)
  }, logical(1L)))) value <- unlist(value, use.names = FALSE)
  if (!is.numeric(value) || length(value) != 2L || !all(is.finite(value)) ||
      !is_positive_ggplot_number(diff(value))) return(NULL)
  unname(value)
}

ggplot_axis_layers <- function(layers) {
  kinds <- c("GeomPoint", "GeomLine", "GeomPath", "GeomStep", "GeomSmooth", "GeomRibbon",
             "GeomArea", "GeomErrorbar", "GeomErrorbarh", "GeomSegment", "GeomBar", "GeomCol",
             "GeomRect", "GeomTile", "GeomRaster", "GeomText", "GeomLabel", "GeomHline", "GeomVline")
  vapply(layers, function(layer) {
    kind <- class(layer$geom)[[1L]]
    if (!kind %in% kinds) return(NA_character_)
    base <- getExportedValue("ggplot2", kind)
    methods <- c("draw_layer", "draw_panel", "draw_group", "handle_na")
    if (!identical(as.list(layer$geom)[methods], as.list(base)[methods])) return(NA_character_)
    kind
  }, character(1L))
}

ggplot_axes_target <- function(built, table) {
  layout <- built$layout
  coord <- layout$coord
  slot <- which(table$layout$name == "panel")
  if (length(slot) != 1L || length(layout$panel_params) != 1L ||
      !identical(class(coord)[[1L]], "CoordCartesian") ||
      length(coord$reverse) != 1L || !coord$reverse %in% c("none", "x", "y", "xy") ||
      !is.null(coord$ratio) || !identical(coord$clip, "on")) return(NULL)
  kinds <- ggplot_axis_layers(built$plot$layers)
  if (!length(kinds) || anyNA(kinds)) return(NULL)
  panel <- layout$panel_params[[1L]]
  if (!all(vapply(panel$guides$guides, function(guide) {
    class(guide)[[1L]] %in% c("GuideAxis", "GuideNone")
  }, logical(1L))) || any(vapply(ggplot_axis_names, function(axis) {
    scale <- panel[[axis]]$scale
    (!is.null(scale$secondary.axis) && !inherits(scale$secondary.axis, "waiver")) ||
      any(vapply(list(scale$breaks, scale$labels, scale$minor_breaks, scale$limits),
                 is.function, logical(1L)))
  }, logical(1L)))) return(NULL)
  axes <- lapply(ggplot_axis_names, function(axis) {
    scale <- panel[[axis]]$scale
    if (!identical(class(scale)[[1L]], "ScaleContinuousPosition") ||
        !inherits(scale$secondary.axis, "waiver") || !is.null(scale$limits) ||
        !inherits(scale$labels, "waiver") || !inherits(scale$breaks, "waiver") ||
        (!is.null(scale$minor_breaks) && !inherits(scale$minor_breaks, "waiver"))) return(NULL)
    trans <- scale$get_transformation()
    mode <- unname(ggplot_axis_modes[trans$name])
    if (length(mode) != 1L || is.na(mode)) return(NULL)
    range <- ggplot_axis_pair(trans$inverse(panel[[paste0(axis, ".range")]]))
    trained <- trans$inverse(scale$get_limits())
    if (length(trained) != 2L || any(!is.finite(trained)) || !is.finite(diff(trained)) ||
        diff(trained) < 0) trained <- NULL
    if (is.null(range)) return(NULL)
    columns <- lapply(built$data, function(data) intersect(names(data), scale$aesthetics))
    coords <- Map(function(data, cols) unlist(data[cols], use.names = FALSE), built$data, columns)
    if (!all(vapply(coords, function(v) is.null(v) || (is.numeric(v) && !is.object(v)), logical(1L)))) {
      return(NULL)
    }
    native <- lapply(coords, function(v) if (length(v)) trans$inverse(v))
    has_infinite <- any(vapply(coords, function(v) any(is.infinite(v)), logical(1L)))
    positive <- !is.null(trained) && all(trained > 0) &&
      all(vapply(native, function(v) all(v[!is.na(v)] > 0 & is.finite(v[!is.na(v)])), logical(1L))) &&
      all(is.na(coord$limits[[axis]]) | coord$limits[[axis]] > 0)
    can_switch <- !is.null(trained) && !has_infinite && !"GeomRaster" %in% kinds
    probability <- positive && all(trained < 1) &&
      all(vapply(native, function(v) all(v[!is.na(v)] < 1), logical(1L))) &&
      all(is.na(coord$limits[[axis]]) | coord$limits[[axis]] < 1)
    options <- if (!can_switch) mode else c(
      "linear", if (mode == "log" || positive) "log", "symlog", if (mode == "logit" || probability) "logit")
    list(mode = mode, range = range, trained = trained, columns = columns, options = options)
  }) |> stats::setNames(ggplot_axis_names) |> Filter(f = Negate(is.null))
  if (!length(axes)) return(NULL)
  # Preserve the saved identities of plots captured before direction was editable.
  identity_axes <- lapply(axes, function(axis) {
    # A new option on a previously editable axis must not invalidate saved overrides.
    if (axis$mode %in% c("linear", "log")) axis$options <- setdiff(axis$options, c("symlog", "logit"))
    axis
  })
  context <- list(kinds, built$data, identity_axes, coord$limits)
  if (coord$reverse != "none") context <- c(context, list(coord$reverse))
  list(slot = slot, axes = axes, reverse = coord$reverse,
       context = rlang::hash(context))
}

ggplot_axis_edits <- function(target, edits) {
  changes <- list()
  for (prop in names(edits)) {
    direction <- startsWith(prop, "invert_")
    axis <- if (direction) substring(prop, 8L) else substr(prop, 1L, 1L)
    spec <- target$axes[[axis]]
    if (is.null(spec) || (!direction && !prop %in% paste0(axis, c("lim", "scale")))) {
      stop("The requested native R axis edit is unavailable.")
    }
    value <- edits[[prop]]
    if (direction) {
      if (!is.logical(value) || length(value) != 1L || is.na(value)) {
        stop("Native R axis direction must be a boolean.")
      }
      changes[[axis]]$inverted <- value
    } else if (endsWith(prop, "lim")) {
      value <- ggplot_axis_pair(value)
      if (is.null(value)) stop("Native R axis limits must be finite and increasing.")
      changes[[axis]]$range <- value
    } else {
      if (!is.character(value) || length(value) != 1L || !value %in% spec$options) {
        stop("This native R axis cannot use the requested scale with its captured data.")
      }
      changes[[axis]]$mode <- value
    }
  }
  changes
}

ggplot_project_axes <- function(built, target, changes) {
  layout <- ggplot_clone(built$layout)
  limits <- layout$coord$limits
  expand <- layout$coord_params$expand
  inverted <- vapply(ggplot_axis_names, function(axis) ggplot_inverted(target$reverse, axis),
                     logical(1L))
  for (axis in names(changes)) {
    spec <- target$axes[[axis]]
    change <- changes[[axis]]
    if (!is.null(change$inverted)) inverted[[axis]] <- change$inverted
    mode <- if (is.null(change$mode)) spec$mode else change$mode
    range <- change$range
    if (mode == "log" && !is.null(range) && any(range <= 0)) {
      stop("Native R logarithmic axis limits must be strictly positive.")
    }
    if (mode == "logit" && !is.null(range) && any(range <= 0 | range >= 1)) {
      stop("Native R logit axis limits must be strictly between zero and one.")
    }
    name <- paste0("panel_scales_", axis)
    scale <- layout[[name]][[1L]]
    if (mode != spec$mode) {
      previous <- scale$get_transformation()
      trans <- switch(mode, linear = scales::transform_identity(), log = scales::transform_log10(),
                      symlog = ggplot_transform_symlog(), logit = scales::transform_logit())
      trained <- trans$transform(spec$trained)
      if (any(!is.finite(trained))) stop("The native R scale has no finite transformed range.")
      scale <- scale$clone()
      scale$trans <- trans
      scale$train(trained)
      layout[[name]] <- list(scale)
      built$data <- Map(function(data, columns) {
        for (field in columns) data[[field]] <- trans$transform(previous$inverse(data[[field]]))
        data
      }, built$data, spec$columns)
    }
    if (!is.null(range)) {
      if (is.null(ggplot_axis_pair(layout[[name]][[1L]]$transform(range)))) {
        stop("The native R axis has no finite transformed view.")
      }
      limits[[axis]] <- range
      expand[if (axis == "x") c(4L, 2L) else c(3L, 1L)] <- FALSE
    }
  }
  reverse <- paste(ggplot_axis_names[inverted], collapse = "")
  if (!nzchar(reverse)) reverse <- "none"
  layout$coord <- ggplot_clone(layout$coord, limits = limits, reverse = reverse)
  layout$coord_params$expand <- expand
  layout$setup_panel_params()
  layout$setup_panel_guides(built$plot$guides, built$plot$layers)
  built$layout <- layout
  built
}

measure_ggplot_axes <- function(table, target, frame_pt) {
  if (is.null(target)) return(NULL)
  on.exit(grid::upViewport(0), add = TRUE)
  grid::grid.force()
  grid::downViewport(ggplot_cell_name(table$layout[target$slot, ]), strict = FALSE)
  bbox <- ggplot_panel_clip(frame_pt)
  if (!all(is.finite(unlist(bbox))) || any(unlist(bbox)[3:4] <= 0)) {
    stop("The native R plotting area could not be measured.")
  }
  bbox
}

ggplot_axes_element <- function(target, current, bbox, edits) {
  if (is.null(target)) return(list())
  original <- lapply(names(target$axes), function(axis) {
    spec <- target$axes[[axis]]
    list(mode = spec$mode, range = as.list(spec$range), options = as.list(spec$options),
         inverted = ggplot_inverted(target$reverse, axis))
  }) |> stats::setNames(names(target$axes))
  if (is.null(current)) current <- original
  list(list(gid = ggplot_axes_gid, role = "axes", label = "\u5b50\u56fe", bbox = bbox,
            draggable = FALSE, editable = list(), native_axes = list(
              context = target$context, current = current, original = original,
              edited = as.list(names(edits)),
              grid = if (!is.null(target$grid)) {
                list(original = target$grid$original, edited = edits[intersect(names(edits), ggplot_grid_keys)])
              },
              background = if (!is.null(target$background)) {
                list(original = target$background$original,
                     current = edits$facecolor %||% target$background$original)
              },
              frame = if (!is.null(target$frame)) {
                edit <- edits[intersect(names(edits), ggplot_frame_keys)]
                list(original = ggplot_frame_value(target$frame, list()),
                     current = ggplot_frame_value(target$frame, edit), edited = edit)
              })))
}

ggplot_rebind_layer_targets <- function(table, targets, grobs) {
  lapply(targets, function(target) {
    panel <- table$grobs[[target$slot]]
    layer <- grobs[[target$index]]
    if (length(layer) != 1L) stop("The native R layer changed while adjusting the axes.")
    name <- layer[[1L]]$name
    child <- which(panel$childrenOrder == name)
    if (length(child) != 1L) stop("The native R layer could not be found after adjusting the axes.")
    if (is.character(target$child)) target$child <- name else target$child <- child
    if (!is.null(target$grob_name)) target$grob_name <- name
    target
  })
}

ggplot_axes_values <- function(layout, target, changes) {
  panel <- layout$panel_params[[1L]]
  lapply(names(target$axes), function(axis) {
    trans <- panel[[axis]]$scale$get_transformation()
    range <- ggplot_axis_pair(trans$inverse(panel[[paste0(axis, ".range")]]))
    if (is.null(range)) stop("The native R axis produced an invalid range.")
    requested <- changes[[axis]]$range
    if (!is.null(requested)) {
      if (!isTRUE(all.equal(range, requested, tolerance = 1e-12))) {
        stop("The native R axis did not apply the requested range.")
      }
      range <- requested
    }
    list(mode = ggplot_axis_modes[[trans$name]], range = as.list(range),
         inverted = ggplot_inverted(panel$reverse, axis))
  }) |> stats::setNames(names(target$axes))
}

ggplot_minor_tick_defaults <- function(built, params, panel, axis) {
  view <- panel[[axis]]
  breaks <- view$get_breaks_minor()
  breaks <- sort(unique(breaks[is.finite(breaks)]))
  if (length(breaks) < 2L) return(NULL)
  guide <- ggplot2::GuideAxis
  resolved <- guide$setup_params(params)
  elements <- guide$setup_elements(resolved, guide$elements, built$plot$theme)
  line <- inherits(elements$minor, "element_line") && inherits(elements$minor_length, "unit") &&
    any(abs(grid::convertUnit(elements$minor_length, "bigpts", valueOnly = TRUE)) > 0)
  step <- if (identical(view$scale$get_transformation()$name, "identity")) {
    stats::median(diff(breaks))
  }
  list(visible = isTRUE(params$minor.ticks) && line &&
         any(params$key$.type == "minor"),
       theme = if (!line) {
         base <- guide$setup_elements(resolved, guide$elements, ggplot2::theme_grey())
         suffix <- paste(axis, params$position, sep = ".")
         replacements <- list(base$minor, base$minor_length)
         names(replacements) <- paste(c("axis.minor.ticks", "axis.minor.ticks.length"), suffix, sep = ".")
         do.call(ggplot2::theme, replacements)
       },
       step = if (is_positive_ggplot_number(step)) step)
}

ggplot_minor_tick_fields <- function(defaults, edit) {
  if (is.null(defaults)) return(list())
  options <- c("auto", if (!is.null(defaults$step)) "step")
  fields <- list(
    list(prop = "minor_visible", type = "bool", value = edit$minor_visible %||% defaults$visible),
    list(prop = "minor_mode", type = "enum", value = edit$minor_mode %||% "auto",
         options = as.list(options), strict_options = TRUE)
  )
  if (!is.null(edit$minor_visible)) fields[[1L]]$value_original <- defaults$visible
  if (!is.null(edit$minor_mode)) fields[[2L]]$value_original <- "auto"
  if (!is.null(defaults$step)) {
    step <- list(prop = "minor_step", type = "number", min = 0, step = defaults$step / 10,
                 value = edit$minor_step %||% defaults$step)
    if (!is.null(edit$minor_step)) step$value_original <- defaults$step
    fields[[3L]] <- step
  }
  fields
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
    breaks <- trans$transform(ggplot_uniform_breaks(limits, step, "minor"))
    panel[[axis]] <- view <- ggplot_clone(view, minor_breaks = breaks)
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

ggplot_grid_keys <- c("grid_x", "grid_y", "grid_color", "grid_linestyle", "grid_linewidth", "grid_alpha")
ggplot_grid_cells <- c(grid_x = "panel.grid.major.x", grid_y = "panel.grid.major.y",
                       minor_x = "panel.grid.minor.x", minor_y = "panel.grid.minor.y")

ggplot_cartesian_panel_slot <- function(built, table) {
  layout <- built$layout
  slot <- which(table$layout$name == "panel")
  if (length(slot) == 1L && length(layout$panel_params) == 1L &&
      identical(class(layout$coord)[[1L]], "CoordCartesian")) slot
}

ggplot_panel_decoration <- function(built, target, slot) {
  if (!is.null(target)) return(target)
  # Keep the identity of decoration-only targets already saved by grid editing.
  list(slot = slot, axes = list(),
       context = rlang::hash(list("grid", built$data, built$layout$coord$limits)))
}

ggplot_grid_target <- function(built, table, target) {
  slot <- ggplot_cartesian_panel_slot(built, table)
  if (is.null(slot)) return(target)
  theme <- built$plot$theme
  lines <- lapply(ggplot_grid_cells, ggplot2::calc_element, theme = theme)
  if (!all(vapply(lines, function(line) {
    inherits(line, "element_blank") || identical(class(line), class(ggplot2::element_line()))
  }, logical(1L)))) return(target)
  visible <- !vapply(lines, inherits, logical(1L), "element_blank")
  for (i in which(!visible & seq_along(lines) <= 2L)) {
    base <- ggplot2::calc_element("panel.grid.major", theme)
    if (inherits(base, "element_blank")) base <- ggplot2::calc_element("line", theme)
    if (!identical(class(base), class(ggplot2::element_line()))) return(target)
    lines[[i]] <- base
  }
  paints <- lapply(lines, function(line) {
    if (!inherits(line, "element_blank")) ggplot_uniform_pigment(line$colour)
  })
  if (any((visible | seq_along(lines) <= 2L) & lengths(paints) == 0L)) return(target)
  preferred <- if (visible[[2L]] && !visible[[1L]]) 2L else 1L
  line <- lines[[preferred]]
  pigment <- paints[[preferred]]
  if (length(line$linetype) != 1L || is.na(line$linetype) ||
      length(line$linewidth) != 1L || !is.finite(line$linewidth)) return(target)
  native_types <- c("blank", "solid", "dashed", "dotted", "dotdash", "longdash", "twodash")
  linetype <- line$linetype
  if (is.numeric(linetype) && linetype %in% 0:6) linetype <- native_types[[linetype + 1L]]
  name <- names(ggplot_line_types)[match(linetype, ggplot_line_types)]
  if (is.na(name)) name <- as.character(linetype)
  target <- ggplot_panel_decoration(built, target, slot)
  target$grid <- list(lines = lines, paints = paints, visible = visible, original = list(
    grid_x = visible[[1L]], grid_y = visible[[2L]], grid_color = pigment$rgb,
    grid_linestyle = name, grid_linewidth = line$linewidth * ggplot2::.pt * ggplot_lwd_pt,
    grid_alpha = pigment$alpha))
  target
}

ggplot_grid_theme <- function(theme, grid, edit) {
  if (is.null(grid)) stop("The requested native R grid edit is unavailable.")
  for (prop in names(edit)) {
    value <- edit[[prop]]
    finite_number <- is.numeric(value) && length(value) == 1L && is.finite(value)
    valid <- switch(prop,
      grid_x = , grid_y = is.logical(value) && length(value) == 1L && !is.na(value),
      grid_color = is.character(value) && length(value) == 1L && !is.na(value) &&
        (value == "none" || grepl("^#[0-9A-Fa-f]{6}$", value)),
      grid_linestyle = is.character(value) && length(value) == 1L &&
        value %in% names(ggplot_line_types),
      grid_linewidth = finite_number && value >= .1 && value <= 10,
      grid_alpha = finite_number && value >= 0 && value <= 1,
      FALSE)
    if (!valid) stop("Invalid native R grid style.")
  }
  replacements <- list()
  styled <- any(c("grid_color", "grid_alpha", "grid_linewidth", "grid_linestyle") %in% names(edit))
  for (i in seq_along(grid$lines)) {
    toggle <- if (i <= 2L) edit[[names(ggplot_grid_cells)[[i]]]]
    if (!styled && is.null(toggle)) next
    if (identical(toggle, FALSE)) {
      replacements[[ggplot_grid_cells[[i]]]] <- ggplot2::element_blank()
      next
    }
    if (!grid$visible[[i]] && !isTRUE(toggle)) next
    line <- grid$lines[[i]]
    if (!is.null(edit$grid_color) || !is.null(edit$grid_alpha)) {
      pigment <- grid$paints[[i]]
      colour <- if (is.null(edit$grid_color)) line$colour else edit$grid_color
      if (identical(colour, "none")) colour <- NA_character_
      alpha <- if (is.null(edit$grid_alpha)) pigment$alpha else edit$grid_alpha
      line$colour <- scales::alpha(colour, alpha)
    }
    if (!is.null(edit$grid_linestyle)) line$linetype <- unname(ggplot_line_types[[edit$grid_linestyle]])
    if (!is.null(edit$grid_linewidth)) line$linewidth <- edit$grid_linewidth / (ggplot2::.pt * ggplot_lwd_pt)
    line$inherit.blank <- FALSE
    replacements[[ggplot_grid_cells[[i]]]] <- line
  }
  theme + do.call(ggplot2::theme, replacements)
}

ggplot_mirror_raster_layer <- function(layer, coord) {
  if (!identical(class(coord)[[1L]], "CoordCartesian") || !identical(class(layer$geom)[[1L]], "GeomRaster") ||
      !identical(ggplot_axis_layers(list(layer)), "GeomRaster")) return(layer)
  geom <- layer$geom
  mirrored <- ggplot_clone(geom, draw_panel = function(data, panel_params, coord, ...) {
    reverse <- panel_params$reverse
    # Raster's native matrix is indexed before the coordinate transform. Render
    # with the same view in forward order, then reflect both the raster and its placement.
    panel_params$reverse <- "none"
    grob <- geom$draw_panel(data, panel_params, coord, ...)
    if (identical(reverse, "none") || inherits(grob, "zeroGrob")) return(grob)
    if (!inherits(grob, "rastergrob") || !identical(grob$just, "centre") ||
        !is.null(grob$vp) || !is.null(grob$hjust) || !is.null(grob$vjust) ||
        !identical(grid::unitType(grob$x), "native") || !identical(grid::unitType(grob$y), "native")) {
      stop("The native R raster no longer has a reversible placement.")
    }
    rows <- seq_len(nrow(grob$raster))
    columns <- seq_len(ncol(grob$raster))
    if (ggplot_inverted(reverse, "x")) {
      columns <- rev(columns)
      grob$x <- grid::unit(1 - as.numeric(grob$x), "native")
    }
    if (ggplot_inverted(reverse, "y")) {
      rows <- rev(rows)
      grob$y <- grid::unit(1 - as.numeric(grob$y), "native")
    }
    grob$raster <- grDevices::as.raster(grob$raster[rows, columns, drop = FALSE])
    grob
  })
  ggplot_clone(layer, geom = mirrored)
}
