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
      (!is.null(coord$ratio) && !is_positive_ggplot_number(coord$ratio)) ||
      !identical(coord$clip, "on")) return(NULL)
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
  # Fixed Cartesian plots originally only offered grid and frame controls.
  if (!is.null(coord$ratio)) context <- ggplot_decoration_context(built)
  list(slot = slot, axes = axes, reverse = coord$reverse,
       context = rlang::hash(context))
}

ggplot_aspect_target <- function(built, table, target) {
  slot <- ggplot_cartesian_panel_slot(built, table)
  theme <- built$plot$theme
  layout <- built$layout
  if (is.null(slot) || !identical(class(layout$facet)[[1L]], "FacetNull") ||
      !"ratio" %in% names(formals(ggplot2::coord_cartesian)) ||
      any(lengths(theme[c("aspect.ratio", "panel.widths", "panel.heights")]) > 0L)) return(target)
  panel <- layout$panel_params[[1L]]
  if (!is_positive_ggplot_number(diff(panel$x.range)) ||
      !is_positive_ggplot_number(diff(panel$y.range))) return(target)
  ratio <- layout$coord$ratio
  if (!is.null(ratio) && !is_positive_ggplot_number(layout$coord$aspect(panel))) return(target)
  target <- ggplot_panel_decoration(built, target, slot)
  target$aspect <- if (is.null(ratio)) "auto" else as.character(ratio)
  target
}

ggplot_edit_aspect <- function(layout, target, value) {
  if (is.null(target$aspect)) stop("The native R axis aspect is unavailable.")
  if (!is.character(value) || length(value) != 1L || is.na(value)) {
    stop("The native R axis aspect must be auto, equal, or a positive number.")
  }
  if (value == "auto") {
    ratio <- NULL
  } else {
    ratio <- if (value == "equal") 1 else suppressWarnings(as.numeric(value))
    if (!is_positive_ggplot_number(ratio)) stop("The native R axis aspect must be positive and finite.")
  }
  coord <- ggplot_clone(layout$coord, ratio = ratio)
  if (!is.null(ratio) && !is_positive_ggplot_number(coord$aspect(layout$panel_params[[1L]]))) {
    stop("The native R axis aspect cannot be represented at the requested ranges.")
  }
  layout$coord <- coord
  layout
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

ggplot_axes_bbox <- function(table, slot, frame_pt) {
  on.exit(grid::upViewport(0), add = TRUE)
  grid::downViewport(ggplot_cell_name(table$layout[slot, ]), strict = FALSE)
  bbox <- ggplot_panel_clip(frame_pt)
  if (!all(is.finite(unlist(bbox))) || any(unlist(bbox)[3:4] <= 0)) {
    stop("The native R plotting area could not be measured.")
  }
  bbox
}

measure_ggplot_axes <- function(table, targets, frame_pt) {
  if (!length(targets)) return(list())
  grid::grid.force()
  lapply(targets, function(target) ggplot_axes_bbox(table, target$slot, frame_pt))
}

ggplot_axes_element <- function(gid, target, current, bbox, edits) {
  original <- lapply(names(target$axes), function(axis) {
    spec <- target$axes[[axis]]
    list(mode = spec$mode, range = as.list(spec$range), options = as.list(spec$options),
         inverted = ggplot_inverted(target$reverse, axis))
  }) |> stats::setNames(names(target$axes))
  if (is.null(current)) current <- original
  label <- "\u5b50\u56fe"
  if (length(target$facet)) label <- paste(label, paste(target$facet, collapse = " / "), sep = " \u00b7 ")
  list(gid = gid, role = "axes", label = label, bbox = bbox,
            draggable = FALSE, editable = list(), native_axes = list(
              context = target$context, current = current, original = original,
              edited = as.list(names(edits)),
              aspect = if (!is.null(target$aspect)) {
                list(original = target$aspect, current = edits$aspect %||% target$aspect)
              },
              grid = if (!is.null(target$grid)) {
                list(original = target$grid$original, edited = edits[intersect(names(edits), ggplot_grid_keys)])
              },
              tick_sides = if (length(target$tick_sides)) {
                list(original = lapply(target$tick_sides, `[[`, "visible"),
                     edited = edits[intersect(names(edits), ggplot_tick_side_keys)])
              },
              background = if (!is.null(target$background)) {
                list(original = target$background$original,
                     current = edits$facecolor %||% target$background$original)
              },
              frame = if (!is.null(target$frame)) {
                edit <- edits[intersect(names(edits), ggplot_frame_keys)]
                list(original = ggplot_frame_value(target$frame, list()),
                     current = ggplot_frame_value(target$frame, edit), edited = edit)
              }))
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

ggplot_tick_side_axes <- c(bottom = "x", top = "x", left = "y", right = "y")
ggplot_tick_side_keys <- paste0("ticks_", names(ggplot_tick_side_axes))

ggplot_mark_elements <- function(params, theme) {
  guide <- ggplot2::GuideAxis
  elements <- guide$setup_elements(guide$setup_params(params), guide$elements, theme)
  elements[c("ticks", "minor", "major_length", "minor_length")]
}

ggplot_tick_side_target <- function(built, table, target) {
  slot <- ggplot_cartesian_panel_slot(built, table)
  if (is.null(slot)) return(target)
  guides <- built$layout$panel_params[[1L]]$guides
  positions <- vapply(guides$params, function(params) params$position %||% "", character(1L))
  if (anyDuplicated(positions) || !all(names(ggplot_tick_side_axes) %in% positions)) return(target)
  sides <- list()
  for (side in names(ggplot_tick_side_axes)) {
    axis <- ggplot_tick_side_axes[[side]]
    index <- match(side, positions)
    params <- guides$params[[index]]
    suffix <- paste(axis, side, sep = ".")
    if (identical(class(guides$guides[[index]])[[1L]], "GuideAxis") &&
        identical(params$aesthetic, axis) &&
        (is.null(params$theme_suffix) || identical(params$theme_suffix, suffix))) {
      paint <- ggplot_mark_elements(params, built$plot$theme)
      sides[[side]] <- list(index = index, visible = inherits(paint$ticks, "element_line"))
    }
  }
  for (side in setdiff(names(ggplot_tick_side_axes), names(sides))) {
    index <- match(side, positions)
    if (!identical(class(guides$guides[[index]])[[1L]], "GuideNone")) next
    axis <- ggplot_tick_side_axes[[side]]
    opposite <- setdiff(names(ggplot_tick_side_axes)[ggplot_tick_side_axes == axis], side)
    source <- sides[[opposite]]$index
    if (is.null(source)) next
    key <- guides$params[[source]]$key
    cross <- setdiff(ggplot_axis_names, axis)
    if (!is.data.frame(key) || !nrow(key) || !all(c(axis, cross) %in% names(key)) ||
        length(unique(key[[cross]])) != 1L || !all(key[[cross]] %in% c(0, 1))) next
    sides[[side]] <- list(index = index, visible = FALSE, mirror = source)
  }
  if (!length(sides)) return(target)
  target <- ggplot_panel_decoration(built, target, slot)
  target$tick_sides <- stats::setNames(sides, paste0("ticks_", names(sides)))
  target
}

ggplot_apply_tick_sides <- function(sides, edit, guides, params, theme) {
  source_params <- params
  source_guides <- guides
  for (prop in names(edit)) {
    spec <- sides[[prop]]
    value <- edit[[prop]]
    if (is.null(spec) || !is.logical(value) || length(value) != 1L || is.na(value)) {
      stop("The requested native R tick side is unavailable.")
    }
    side <- sub("^ticks_", "", prop)
    axis <- ggplot_tick_side_axes[[side]]
    suffix <- paste(axis, side, sep = ".")
    mark_fields <- paste(c("axis.ticks", "axis.minor.ticks", "axis.ticks.length",
                           "axis.minor.ticks.length"), suffix, sep = ".")
    index <- spec$index
    current <- params[[index]]
    mirror <- spec$mirror
    if (!is.null(mirror) && !value) next
    ref <- if (is.null(mirror)) current else source_params[[mirror]]
    paint <- ggplot_mark_elements(ref, theme)
    if (!is.null(mirror)) {
      # The primary guide has already been projected and edited. Reflect only
      # its perpendicular coordinate so the other side shares the same marks.
      current <- ref
      current$position <- side
      current$theme_suffix <- NULL
      current$title <- NULL
      cross <- setdiff(ggplot_axis_names, axis)
      current$key[[cross]] <- 1 - current$key[[cross]]
      guides[[index]] <- source_guides[[mirror]]
    }
    if (value && !inherits(paint$ticks, "element_line")) {
      fallback <- ggplot_mark_elements(list(position = side), ggplot2::theme_grey())
      paint$ticks <- fallback$ticks
      paint$ticks$inherit.blank <- FALSE
    }
    marks <- if (value) paint else rep(list(ggplot2::element_blank()), 2L)
    elements <- stats::setNames(marks, mark_fields[seq_along(marks)])
    if (!is.null(mirror)) {
      for (style in c("axis.title", "axis.text", "axis.line")) {
        elements[[paste(style, suffix, sep = ".")]] <- ggplot2::element_blank()
      }
    }
    patch <- do.call(ggplot2::theme, elements)
    current$theme <- if (is.null(current$theme)) patch else current$theme + patch
    params[[index]] <- current
  }
  list(guides = guides, params = params)
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

ggplot_decoration_context <- function(built) {
  # Keep the identity of decoration-only targets already saved by grid editing.
  list("grid", built$data, built$layout$coord$limits)
}

ggplot_panel_decoration <- function(built, target, slot) {
  if (!is.null(target)) return(target)
  list(slot = slot, axes = list(), context = rlang::hash(ggplot_decoration_context(built)))
}

ggplot_grid_target <- function(built, table, target, slot = ggplot_cartesian_panel_slot(built, table)) {
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

ggplot_facet_grill <- function(panel) {
  child <- which(startsWith(panel$childrenOrder, "grill."))
  if (length(child) == 1L && inherits(panel$children[[child]], "gTree")) child
}

ggplot_faceted_axes_targets <- function(built, table, panels) {
  if (is.null(panels) || panels$single || !identical(as.list(built$layout$coord)$render_bg,
                                                    as.list(ggplot2::CoordCartesian)$render_bg)) return(list())
  cells <- panels$cells
  ids <- as.integer(as.character(cells$PANEL))
  # The stock facets name gtable panels by physical column and row. Their
  # order in the gtable can differ from the order of native PANEL ids.
  slots <- match(paste("panel", cells$COL, cells$ROW, sep = "-"), table$layout$name)
  if (anyNA(slots) || anyDuplicated(slots) ||
      !identical(sort(ids), seq_along(built$layout$panel_params))) return(list())
  context <- rlang::hash(ggplot_decoration_context(built))
  targets <- lapply(seq_along(ids), function(i) {
    slot <- slots[[i]]
    if (!inherits(table$grobs[[slot]], "gTree")) return(NULL)
    facet <- lapply(cells[i, panels$facets, drop = FALSE], as.character)
    target <- list(slot = slot, panel = ids[[i]], facet = unlist(facet, use.names = FALSE),
                   context = rlang::hash(list(context, facet)))
    if (!is.null(ggplot_facet_grill(table$grobs[[slot]]))) {
      target <- ggplot_grid_target(built, table, target, slot)
      # With panel.ontop the grill is drawn over the data. An opaque fill
      # on that grill would cover the marks instead of painting behind them.
      if (!isTRUE(built$plot$theme$panel.ontop)) {
        target <- ggplot_background_target(built, table, target, slot)
      }
    }
    target <- ggplot_frame_target(built, table, target, slot, panel_only = TRUE)
    if (!all(vapply(target[c("grid", "background", "frame")], is.null, logical(1L)))) target
  })
  targets <- stats::setNames(targets, paste0("axes_", ids))
  Filter(Negate(is.null), targets)
}

ggplot_edit_facet_axes <- function(table, layout, theme, target, edit) {
  if (!all(names(edit) %in% c(ggplot_grid_keys, ggplot_frame_keys, "facecolor"))) {
    stop("The requested native R facet axis edit is unavailable.")
  }
  grid <- edit[intersect(names(edit), ggplot_grid_keys)]
  if (length(grid) || !is.null(edit$facecolor)) {
    if (length(grid)) theme <- ggplot_grid_theme(theme, target$grid, grid)
    if (!is.null(edit$facecolor)) {
      theme <- theme + ggplot2::theme(panel.background = ggplot_background_style(target$background, edit$facecolor))
    }
    panel <- table$grobs[[target$slot]]
    child <- ggplot_facet_grill(panel)
    if (is.null(child)) stop("The native R facet grid no longer matches its captured layout.")
    grill <- layout$coord$render_bg(layout$panel_params[[target$panel]], theme)
    grill$name <- panel$childrenOrder[[child]]
    panel$children[[child]] <- grill
    table$grobs[[target$slot]] <- panel
  }
  frame <- edit[intersect(names(edit), ggplot_frame_keys)]
  if (length(frame)) {
    ggplot_validate_frame_edits(target$frame, frame)
    table <- ggplot_frame_edit(table, target$slot, target$frame$fallback, frame, panel_only = TRUE)
  }
  table
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
