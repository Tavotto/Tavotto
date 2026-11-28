ggplot_axis_modes <- c(identity = "linear", "log-10" = "log",
                       "tavotto-symlog" = "symlog", "prob-logis" = "logit")
ggplot_axis_names <- c("x", "y")

ggplot_inverted <- function(reverse, axis) isTRUE(grepl(axis, reverse, fixed = TRUE))

ggplot_axis_pair <- function(value) {
  if (is.list(value) && length(value) == 2L && all(vapply(value, function(end) {
    is.numeric(end) && length(end) == 1L && is.finite(end)
  }, logical(1L)))) value <- unlist(value, use.names = FALSE)
  if (!is.numeric(value) || length(value) != 2L || !all(is.finite(value)) ||
      !is.finite(diff(value)) || diff(value) <= 0) return(NULL)
  unname(value)
}

ggplot_clone_secondary <- function(axis, breaks) {
  axis <- axis$tavotto_secondary_base %||% axis
  if (identical(as.list(axis)$trans, base::identity)) return(ggplot_clone(axis, breaks = breaks))
  ggplot_clone(axis, breaks = breaks, tavotto_secondary_base = axis, transform_range = function(self, range) {
    # Validate the values actually used by the native guide, including the
    # display window. Its stock monotonicity test only checks trained data.
    values <- ggplot2::ggproto_parent(axis, self)$transform_range(range)
    if (!is.numeric(values) || length(values) != length(range) || !all(is.finite(values))) {
      stop("The native R secondary axis cannot describe this range.")
    }
    direction <- diff(values)
    if (!(all(direction > 0) || all(direction < 0))) {
      stop("The native R secondary axis must stay strictly monotonic across this range.")
    }
    values
  })
}

ggplot_secondary_breaks <- function(scale) {
  axis <- scale$secondary.axis
  if (!inherits(axis, "AxisSecondary")) return(NULL)
  native <- as.list(axis)$breaks
  if (inherits(native, "derived") || inherits(native, "waiver") ||
      identical(native, scale$get_transformation()$breaks)) return("automatic")
  if (!is.function(native)) return("fixed")
  # With n.breaks, ggplot2 wraps its locator with the requested count.
  # Compare the native implementation and its referenced bindings so a
  # user-defined locator is never mistaken for that generated callback.
  probe <- ggplot2::dup_axis()
  probe$init(scale)
  expected <- as.list(probe)$breaks
  if (is.function(expected) && identical(formals(expected), formals(native)) &&
      identical(body(expected), body(native))) {
    bindings <- intersect(all.names(body(expected), unique = TRUE), ls(environment(expected), all.names = TRUE))
    if (length(bindings) && identical(mget(bindings, environment(expected), inherits = FALSE),
        mget(bindings, environment(native), inherits = FALSE, ifnotfound = list(NULL)))) return("automatic")
  }
  "custom"
}

ggplot_axis_expand_sides <- function(axis) if (axis == "x") c(4L, 2L) else c(3L, 1L)

ggplot_projection_training <- function(scale, data) {
  trans <- scale$get_transformation()
  mode <- unname(ggplot_axis_modes[trans$name])
  if (!identical(class(scale)[[1L]], "ScaleContinuousPosition") ||
      length(mode) != 1L || is.na(mode)) return(NULL)
  trained <- trans$inverse(scale$get_limits())
  if (length(trained) != 2L || any(!is.finite(trained)) || !is.finite(diff(trained)) ||
      diff(trained) < 0) trained <- NULL
  list(mode = mode, trained = trained, secondary_breaks = ggplot_secondary_breaks(scale),
       columns = lapply(data, function(rows) intersect(names(rows), scale$aesthetics)))
}

ggplot_project_axis_scale <- function(scale, spec, change) {
  mode <- change$mode %||% spec$mode
  range <- change$range
  if (mode == "log" && !is.null(range) && any(range <= 0)) {
    stop("Native R logarithmic axis limits must be strictly positive.")
  }
  if (mode == "logit" && !is.null(range) && any(range <= 0 | range >= 1)) {
    stop("Native R logit axis limits must be strictly between zero and one.")
  }
  convert <- NULL
  if (mode != spec$mode) {
    if (is.null(spec$trained) || identical(spec$secondary_breaks, "custom")) {
      stop("The native R scale cannot be reprojected.")
    }
    previous <- scale$get_transformation()
    trans <- switch(mode, linear = scales::transform_identity(), log = scales::transform_log10(),
                    symlog = ggplot_transform_symlog(), logit = scales::transform_logit())
    trained <- trans$transform(spec$trained)
    if (any(!is.finite(trained))) stop("The native R scale has no finite transformed range.")
    scale <- scale$clone()
    scale$trans <- trans
    scale$train(trained)
    convert <- function(values) {
      projected <- suppressWarnings(trans$transform(previous$inverse(values)))
      if (any(!is.na(values) & !is.finite(projected))) {
        stop("The native R scale cannot represent all plotted positions.")
      }
      projected
    }
  }
  secondary <- scale$secondary.axis
  if (inherits(secondary, "AxisSecondary")) {
    original <- spec$secondary_breaks
    breaks <- if (!is.null(convert) && identical(original, "automatic")) ggplot2::derive() else as.list(secondary)$breaks
    scale <- ggplot_clone(scale, secondary.axis = ggplot_clone_secondary(secondary, breaks))
  }
  if (!is.null(range) && is.null(ggplot_axis_pair(scale$transform(range)))) {
    stop("The native R axis has no finite transformed view.")
  }
  list(scale = scale, convert = convert)
}

ggplot_convert_axis_data <- function(data, columns, rows, convert) {
  for (field in columns) data[[field]][rows] <- convert(data[[field]][rows])
  data
}

ggplot_facet_view_coord <- function(original, scales, views) {
  force(scales)
  force(views)
  ggplot_clone(original, tavotto_facet_view_base = original, tavotto_facet_views = views,
               setup_panel_params = function(scale_x, scale_y, params = list()) {
    active <- list(x = scale_x, y = scale_y)
    limits <- original$limits
    inverted <- vapply(ggplot_axis_names, function(axis) ggplot_inverted(original$reverse, axis), logical(1L))
    for (axis in names(views)) {
      id <- which(vapply(scales[[axis]], identical, logical(1L), active[[axis]]))
      if (length(id) != 1L) stop("The native R facet view could not find its position scale.")
      edit <- views[[axis]][[as.character(id)]]
      if (!is.null(edit$range)) {
        limits[[axis]] <- edit$range
        params$expand[ggplot_axis_expand_sides(axis)] <- FALSE
      }
      if (!is.null(edit$inverted)) inverted[[axis]] <- edit$inverted
    }
    reverse <- paste(ggplot_axis_names[inverted], collapse = "")
    coord <- ggplot_clone(original, limits = limits, reverse = if (nzchar(reverse)) reverse else "none")
    coord$setup_panel_params(scale_x, scale_y, params)
  })
}

# Both the editor and the emitted R script project the already computed
# positions. Group keys are native SCALE_X/SCALE_Y indices for this build.
ggplot_project_facet_positions <- function(layout, data, groups, changes) {
  original <- layout$coord$tavotto_facet_view_base %||% layout$coord
  views <- layout$coord$tavotto_facet_views %||% list()
  for (axis in names(changes)) {
    field <- paste0("panel_scales_", axis)
    for (id in names(changes[[axis]])) {
      group <- groups[[axis]][[id]]
      index <- as.integer(id)
      change <- utils::modifyList(views[[axis]][[id]] %||% list(), changes[[axis]][[id]])
      revised <- ggplot_project_axis_scale(layout[[field]][[index]], group$spec, change)
      layout[[field]][[index]] <- revised$scale
      views[[axis]][[id]] <- change[intersect(names(change), c("range", "inverted"))]
      if (!is.null(revised$convert)) {
        data <- Map(function(rows, columns, indices) {
          ggplot_convert_axis_data(rows, columns, indices, revised$convert)
        }, data, group$spec$columns, group$rows)
      }
    }
  }
  scales <- list(x = layout$panel_scales_x, y = layout$panel_scales_y)
  layout$coord <- ggplot_facet_view_coord(original, scales, views)
  layout$setup_panel_params()
  data
}

ggplot_source_facet_layout_supported <- function(layout) {
  base <- layout$tavotto_facet_view_layout %||% layout
  fields <- c("map_position", "setup_panel_params", "setup_panel_guides")
  identical(as.list(base)[fields], as.list(ggplot2::Layout)[fields])
}

ggplot_source_facet_scale_supported <- function(plot) {
  finish <- as.list(ggplot2::Stat)$finish_layer
  all(vapply(plot$layers, function(layer) {
    mapping <- layer$computed_mapping
    if (is.null(mapping)) {
      inherited <- if (isTRUE(layer$inherit.aes)) as.list(plot$mapping) else list()
      mapping <- utils::modifyList(inherited, as.list(layer$mapping))
    }
    identical(as.list(layer$stat)$finish_layer, finish) &&
      !any(vapply(mapping, function(value) {
        any(all.names(rlang::get_expr(value)) %in% c("after_scale", "stage"))
      }, logical(1L)))
  }, logical(1L)))
}
