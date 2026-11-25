# Translate the canonical colorbar (vmin, vmax, cmap) to a trained native
# scale. Capture scalars where ggplot2 maps them, after stats and positions.
# The same replacement scale supplies the plot colors and the guide.

ggplot_observe_colour_maps <- function(plot, capture) {
  original <- plot$scales
  plot$scales <- ggplot_clone(original, non_position_scales = function(self, ...) {
    scales <- ggplot2::ggproto_parent(original, self)$non_position_scales(...)
    ggplot_clone(scales, map_df = function(self, df, ...) {
      mapped <- ggplot2::ggproto_parent(scales, self)$map_df(df, ...)
      channels <- intersect(c("colour", "fill"), names(df))
      row_keys <- intersect(c("PANEL", "group", "x", "y", "xmin", "xmax", "ymin", "ymax"), names(df))
      capture$maps[[length(capture$maps) + 1L]] <- list(
        input = df[channels], output = mapped[channels], rows = df[row_keys]
      )
      mapped
    })
  })
  plot
}

ggplot_colour_members <- function(built, maps, channels) {
  if (length(maps) != length(built$data) || !length(maps)) return(NULL)
  members <- list()
  for (i in seq_along(maps)) {
    observed <- maps[[i]]
    data <- built$data[[i]]
    values <- observed$input[intersect(channels, names(observed$input))]
    if (!length(values) || !nrow(values)) next
    layer <- built$plot$layers[[i]]
    # These stock drawing methods consume final data directly. We can redraw
    # them without invoking a stat, aesthetic expression or custom draw hook.
    if (!class(layer$geom)[[1L]] %in% c("GeomPoint", "GeomTile", "GeomRect", "GeomRaster") ||
        !identical(observed$rows, data[names(observed$rows)]) ||
        any(vapply(layer$computed_mapping, function(mapping) {
          any(all.names(rlang::get_expr(mapping)) %in% c("after_scale", "stage"))
        }, logical(1L)))) return(NULL)
    if (!all(vapply(names(values), function(channel) {
      raw <- values[[channel]]
      is.numeric(raw) && !is.object(raw) && length(raw) == nrow(data) &&
        identical(observed$output[[channel]], data[[channel]]) && is.character(data[[channel]])
    }, logical(1L)))) return(NULL)
    members[[as.character(i)]] <- values
  }
  members
}

ggplot_colour_scale_targets <- function(built, table, text_targets, titles, maps) {
  if (!identical(class(built$layout$coord)[[1L]], "CoordCartesian")) return(list())
  targets <- list()
  for (gid in grep("^legend\\.[^.]+\\.[0-9]+\\.title$", names(text_targets), value = TRUE)) {
    title <- text_targets[[gid]]
    index <- match(title$label, unname(titles))
    if (is.na(index)) next
    key <- names(titles)[[index]]
    params <- built$plot$guides$params[[key]]
    aesthetic <- params$aesthetic
    if (length(aesthetic) != 1L || !aesthetic %in% c("colour", "fill")) next
    scale <- built$plot$scales$get_scales(aesthetic)
    if (!identical(class(scale)[[1L]], "ScaleContinuous") ||
        !identical(scale$get_transformation()$name, "identity") ||
        !all(scale$aesthetics %in% c("colour", "fill"))) next
    limits <- scale$get_limits()
    if (length(limits) != 2L || !all(is.finite(limits)) || !is_positive_ggplot_number(diff(limits))) next
    # ggplot2 names the guide decorations `colour` even for a fill scale.
    samples <- params$decor$colour
    if (!is.character(samples) || length(samples) < 2L || anyNA(samples)) next
    members <- ggplot_colour_members(built, maps, scale$aesthetics)
    if (!length(members)) next
    slots <- title$slots[1:2]
    guide <- table$grobs[[slots[[1L]]]]$grobs[[slots[[2L]]]]
    if (sum(guide$layout$name == "bar") != 1L) next
    target_gid <- sub("\\.title$", ".colourbar", gid)
    stops <- samples[round(seq(1, length(samples), length.out = 9L))]
    targets[[target_gid]] <- list(
      index = key, title = title$label, aesthetic = aesthetic, scale = scale,
      host = sub("\\.[0-9]+\\.colourbar$", "", target_gid),
      slots = slots, limits = limits, stops = stops, members = members,
      range_editable = inherits(scale$labels, "waiver"),
      context = rlang::hash(list(title$label, aesthetic, limits, stops, members))
    )
  }
  # Multiple native guides can present the same scale (e.g. colour and fill
  # on opposite sides). Register one writable object and translate its
  # edits to every native guide for that scale.
  scale_keys <- vapply(targets, function(target) paste(sort(target$scale$aesthetics), collapse = ":"), character(1L))
  result <- list()
  guide_set <- built$plot$guides
  guide_keys <- vapply(targets, function(target) target$index, character(1L))
  for (key in unique(scale_keys)) {
    gid <- names(scale_keys)[which(scale_keys == key)[[1L]]]
    target <- targets[[gid]]
    associated <- Filter(function(params) any(params$aesthetic %in% target$scale$aesthetics),
                         guide_set$params)
    descriptors <- list()
    for (index in names(associated)) {
      guide <- guide_set$guides[[index]]
      aesthetic <- associated[[index]]$aesthetic
      if (!identical(class(guide)[[1L]], "GuideColourbar") || length(aesthetic) != 1L) break
      alias <- names(guide_keys)[match(index, guide_keys)]
      descriptors[[index]] <- list(aesthetic = aesthetic,
                                    ticks = if (!is.na(alias)) paste0(alias, ".ticks"))
    }
    if (length(descriptors) != length(associated)) next
    target$guides <- descriptors
    result[[gid]] <- target
  }
  result
}

measure_ggplot_colour_scales <- function(table, targets, frame_pt) {
  if (!length(targets)) return(list())
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    box <- table$grobs[[target$slots[[1L]]]]
    guide <- box$grobs[[target$slots[[2L]]]]
    slot <- which(guide$layout$name == "bar")
    if (length(slot) != 1L) next
    bar <- guide$grobs[[slot]]
    path <- c(table$name, ggplot_cell_name(table$layout[target$slots[[1L]], ]),
              box$name, ggplot_cell_name(box$layout[target$slots[[2L]], ]),
              guide$name, ggplot_cell_name(guide$layout[slot, ]))
    row <- which(drawn$name == bar$name & drawn$gPath == paste(path, collapse = "::"))
    if (length(row) != 1L) next
    viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
    if (length(viewport) < 2L || viewport[[1L]] != "ROOT") next
    result[[gid]] <- tryCatch({
      grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
      lower <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
      upper <- grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE)
      bbox <- ggplot_bbox(lower, upper, frame_pt)
      if (all(is.finite(bbox)) && all(bbox[3:4] > 0)) bbox
    }, error = function(error) NULL, finally = grid::upViewport(0))
  }
  result
}

ggplot_palette <- function(catalog, name) {
  packed <- catalog$palettes[[name]]
  size <- catalog$size
  if (is.null(packed)) stop("Unknown Tavotto palette.")
  starts <- seq.int(1L, nchar(packed), by = 6L)
  colors <- paste0("#", substring(packed, starts, starts + 5L))
  function(x) {
    index <- pmax(1, pmin(floor(x * size) + 1, size))
    colors[index]
  }
}

ggplot_colour_scale_state <- function(target, edit, catalog) {
  if (!all(names(edit) %in% c("vmin", "vmax", "cmap"))) stop("Invalid native R colour scale edit.")
  limits <- target$limits
  if (any(c("vmin", "vmax") %in% names(edit))) {
    if (!target$range_editable) stop("This native R colour scale uses custom labels.")
    for (i in seq_along(limits)) {
      value <- edit[[c("vmin", "vmax")[[i]]]]
      if (is.null(value)) next
      if (!is.numeric(value) || length(value) != 1L || !is.finite(value)) {
        stop("Native R colour limits must be finite numbers.")
      }
      limits[[i]] <- value
    }
    if (!is_positive_ggplot_number(diff(limits))) stop("The lower colour limit must be below the upper limit.")
  }
  name <- edit$cmap
  if (!is.null(name) && (!is.character(name) || length(name) != 1L ||
                        !name %in% names(catalog$palettes))) stop("Unknown Tavotto palette.")
  list(limits = limits, cmap = name, range_changed = !identical(limits, target$limits))
}

ggplot_remap_colour_scales <- function(state, edits, guide_params) {
  built <- state$built
  data <- built$data
  changed <- integer()
  ticks <- list()
  stops <- list()
  guide_ticks <- character()
  for (gid in names(edits)) {
    target <- state$colour_scales[[gid]]
    selection <- ggplot_colour_scale_state(target, edits[[gid]], state$palettes)
    scale <- target$scale
    scale <- ggplot_clone(scale)
    if (selection$range_changed) {
      scale$limits <- selection$limits
      scale$breaks <- ggplot2::waiver()
      # Extrema use endpoint colors, like the standard Tavotto color scale.
      # Preserve missing values and the source scale's NA color.
      scale$oob <- scales::squish
    }
    if (!is.null(selection$cmap)) scale$palette <- ggplot_palette(state$palettes, selection$cmap)
    for (key in names(target$guides)) {
      guide <- built$plot$guides$guides[[key]]
      descriptor <- target$guides[[key]]
      params <- guide$train(params = guide_params[[key]], scale = scale,
                            aesthetic = descriptor$aesthetic)
      if (is.null(params)) stop("The native R colour bar could not be drawn for this range.")
      guide_params[[key]] <- params
      if (!is.null(descriptor$ticks)) {
        tick_gid <- descriptor$ticks
        guide_ticks <- c(guide_ticks, tick_gid)
        labels <- list(tick_labels = as.list(params$key$.label))
        ticks[[tick_gid]] <- ggplot_numeric_key(params, labels, ggplot_colourbar_key_values(params))
      }
      if (identical(key, target$index)) {
        colours <- params$decor$colour
        stops[[gid]] <- colours[round(seq(1, length(colours), length.out = 9L))]
      }
    }
    for (layer in names(target$members)) {
      index <- as.integer(layer)
      values <- target$members[[layer]]
      for (channel in names(values)) data[[index]][[channel]] <- scale$map(values[[channel]])
      changed <- c(changed, index)
    }
  }
  list(data = data, changed = unique(changed), guides = guide_params, ticks = ticks,
       guide_ticks = guide_ticks, stops = stops)
}

ggplot_colour_scale_elements <- function(state, edits, measured, stops) {
  lapply(names(state$colour_scales), function(gid) {
    target <- state$colour_scales[[gid]]
    selection <- ggplot_colour_scale_state(target, edits[[gid]], state$palettes)
    bbox <- if (is.null(measured)) target$bbox else measured[[gid]]
    if (is.null(bbox)) stop("The native R colour bar could not be measured.")
    current <- if (is.null(stops[[gid]])) target$stops else stops[[gid]]
    element <- list(gid = gid, role = "colorbar", label = target$title, bbox = as.list(bbox),
         draggable = FALSE, editable = list(), color_scale = list(
           context = target$context, limits = as.list(selection$limits),
           original_limits = as.list(target$limits), stops = as.list(target$stops),
           current_stops = as.list(current),
           cmap = selection$cmap, range_editable = target$range_editable
         ))
    if (target$host %in% names(state$legend_targets)) element$drag_host <- target$host
    element
  })
}
