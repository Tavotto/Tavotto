# Reproject the captured positions in original data units. The source's
# stat, group, and position results stay the inputs to the stock geoms.
ggplot_axes_gid <- "axes_0"
ggplot_axis_modes <- c(identity = "linear", "log-10" = "log")

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
      !identical(class(coord)[[1L]], "CoordCartesian") || !identical(coord$reverse, "none") ||
      !is.null(coord$ratio) || !identical(coord$clip, "on")) return(NULL)
  kinds <- ggplot_axis_layers(built$plot$layers)
  if (!length(kinds) || anyNA(kinds)) return(NULL)
  panel <- layout$panel_params[[1L]]
  if (!all(vapply(panel$guides$guides, function(guide) {
    class(guide)[[1L]] %in% c("GuideAxis", "GuideNone")
  }, logical(1L))) || any(vapply(c("x", "y"), function(axis) {
    scale <- panel[[axis]]$scale
    (!is.null(scale$secondary.axis) && !inherits(scale$secondary.axis, "waiver")) ||
      any(vapply(list(scale$breaks, scale$labels, scale$minor_breaks, scale$limits),
                 is.function, logical(1L)))
  }, logical(1L)))) return(NULL)
  axes <- lapply(c("x", "y"), function(axis) {
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
    options <- if (!can_switch) mode else if (mode == "log" || positive) c("linear", "log") else "linear"
    list(mode = mode, range = range, trained = trained, columns = columns, options = options)
  }) |> stats::setNames(c("x", "y")) |> Filter(f = Negate(is.null))
  if (!length(axes)) return(NULL)
  list(slot = slot, axes = axes, context = rlang::hash(list(kinds, built$data, axes, coord$limits)))
}

ggplot_axis_edits <- function(target, edits) {
  changes <- list()
  for (prop in names(edits)) {
    axis <- substr(prop, 1L, 1L)
    spec <- target$axes[[axis]]
    if (is.null(spec) || !prop %in% paste0(axis, c("lim", "scale"))) {
      stop("The requested native R axis edit is unavailable.")
    }
    value <- edits[[prop]]
    if (endsWith(prop, "lim")) {
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
  for (axis in names(changes)) {
    spec <- target$axes[[axis]]
    change <- changes[[axis]]
    mode <- if (is.null(change$mode)) spec$mode else change$mode
    range <- change$range
    if (mode == "log" && !is.null(range) && any(range <= 0)) {
      stop("Native R logarithmic axis limits must be strictly positive.")
    }
    name <- paste0("panel_scales_", axis)
    scale <- layout[[name]][[1L]]
    if (mode != spec$mode) {
      previous <- scale$get_transformation()
      trans <- if (mode == "log") scales::transform_log10() else scales::transform_identity()
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
  layout$coord <- ggplot_clone(layout$coord, limits = limits)
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
  original <- lapply(target$axes, function(axis) {
    list(mode = axis$mode, range = as.list(axis$range), options = as.list(axis$options))
  })
  if (is.null(current)) current <- original
  list(list(gid = ggplot_axes_gid, role = "axes", label = "\u5b50\u56fe", bbox = bbox,
            draggable = FALSE, editable = list(), native_axes = list(
              context = target$context, current = current, original = original,
              edited = as.list(names(edits)))))
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
    list(mode = ggplot_axis_modes[[trans$name]], range = as.list(range))
  }) |> stats::setNames(names(target$axes))
}
