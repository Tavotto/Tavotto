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

ggplot_colourbar_directions <- c("vertical", "horizontal")
ggplot_colourbar_extends <- c("neither", "both", "min", "max")
ggplot_colourbar_tip_prefix <- "tavotto-colourbar-end-"

ggplot_colourbar_endpoints <- function(params, inverse) {
  values <- params$decor$value
  colours <- params$decor$colour
  if (!is.numeric(values) || length(values) < 2L || any(!is.finite(values)) ||
      !is.character(colours) || length(colours) != length(values) || anyNA(colours)) return(NULL)
  # Guide decorations use transformed units. A descending scale can reverse
  # the data endpoints even when the guide's reverse flag is false.
  native <- tryCatch(suppressWarnings(inverse(values[c(1L, length(values))])),
                     error = function(error) NULL)
  if (!is.numeric(native) || length(native) != 2L || any(!is.finite(native)) ||
      native[[1L]] == native[[2L]]) return(NULL)
  stats::setNames(c(1L, length(values))[order(native)], c("min", "max"))
}

ggplot_extend_colourbar <- function(table, params, extend, endpoints) {
  slot <- which(table$layout$name == "bar")
  if (length(slot) != 1L || is.null(endpoints)) stop("The native R colourbar has no drawable endpoints.")
  horizontal <- identical(params$direction, "horizontal")
  for (side in intersect(c("min", "max"), if (extend == "both") c("min", "max") else extend)) {
    # Re-read the bar cell after inserting either end. The gtable grows around
    # the native strip, whose length and data-facing tick positions stay intact.
    bar <- table$layout[slot, ]
    endpoint <- endpoints[[side]]
    tip <- as.integer(endpoint != 1L)
    paint <- grid::gpar(fill = params$decor$colour[[endpoint]], col = NA)
    name <- paste0(ggplot_colourbar_tip_prefix, side)
    if (horizontal) {
      index <- if (tip == 0L) bar$l else bar$r + 1L
      length <- sum(table$widths[bar$l:bar$r]) * .05
      table <- gtable::gtable_add_cols(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(1 - tip, 1 - tip, tip), y = c(0, 1, .5), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = bar$t, l = index, b = bar$b, name = name)
    } else {
      index <- if (tip == 1L) bar$t else bar$b + 1L
      length <- sum(table$heights[bar$t:bar$b]) * .05
      table <- gtable::gtable_add_rows(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(0, 1, .5), y = c(1 - tip, 1 - tip, tip), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = index, l = bar$l, r = bar$r, name = name)
    }
  }
  table
}

ggplot_capture_colourbar <- function(guide, key, inverse, extend) {
  force(guide)
  force(key)
  force(inverse)
  force(extend)
  ggplot_clone(guide, assemble_drawing = function(self, grobs, layout, sizes, params, elems) {
    table <- guide$assemble_drawing(grobs, layout, sizes, params, elems)
    endpoints <- ggplot_colourbar_endpoints(params, inverse)
    if (!is.null(extend)) table <- ggplot_extend_colourbar(table, params, extend, endpoints)
    attr(table, "tavotto_colourbar") <- list(
      index = key, orientation = params$direction, can_extend = !is.null(endpoints),
      reversed = if (is.null(endpoints)) isTRUE(params$reverse) else endpoints[["min"]] != 1L
    )
    table
  })
}

ggplot_colourbar_guides <- function(guides, scales, extensions = NULL) {
  actual <- guides$guides
  for (key in names(actual)) {
    guide <- actual[[key]]
    if (identical(class(guide)[[1L]], "GuideColourbar") &&
        identical(as.list(guide)$assemble_drawing, as.list(ggplot2::GuideColourbar)$assemble_drawing)) {
      aesthetic <- guides$params[[key]]$aesthetic
      if (length(aesthetic) != 1L) next
      scale <- scales$get_scales(aesthetic)
      if (is.null(scale)) next
      actual[[key]] <- ggplot_capture_colourbar(
        guide, key, scale$get_transformation()$inverse, extensions[[key]]
      )
    }
  }
  ggplot_clone(guides, guides = actual)
}

ggplot_colour_scale_mappings <- function(built, table, text_targets, titles, maps) {
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

ggplot_colour_scale_targets <- function(built, table, text_targets, titles, maps) {
  mappings <- ggplot_colour_scale_mappings(built, table, text_targets, titles, maps)
  targets <- list()
  guides <- ggplot_legend_guides(table)
  for (prefix in names(guides)) {
    item <- guides[[prefix]]
    meta <- attr(item$guide, "tavotto_colourbar", exact = TRUE)
    if (is.null(meta) || !meta$orientation %in% ggplot_colourbar_directions ||
        sum(item$guide$layout$name == "bar") != 1L) next
    gid <- paste0(prefix, ".colourbar")
    mapping <- mappings[[gid]]
    params <- built$plot$guides$params[[meta$index]]
    label <- params$title
    if (!is.character(label) || length(label) != 1L || is.na(label) || !nzchar(label)) {
      label <- "\u8272\u6761"
    }
    if (is.null(mapping)) {
      context <- rlang::hash(list(item$host, params$title, params$aesthetic,
                                 params$key, params$decor, meta$orientation))
      mapping <- list(index = meta$index, title = label, host = item$host, slots = item$slots,
                      context = context)
    }
    targets[[gid]] <- c(mapping, list(orientation = meta$orientation, can_extend = meta$can_extend,
                                    reversed = meta$reversed))
  }
  for (gid in setdiff(names(mappings), names(targets))) {
    targets[[gid]] <- c(mappings[[gid]], list(can_extend = FALSE))
  }
  targets
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
    meta <- attr(guide, "tavotto_colourbar", exact = TRUE)
    if ((!is.null(target$orientation) && !identical(meta$index, target$index)) ||
        sum(guide$layout$name == "bar") != 1L) next
    cells <- which(guide$layout$name == "bar" | startsWith(guide$layout$name, ggplot_colourbar_tip_prefix))
    path <- c(table$name, ggplot_cell_name(table$layout[target$slots[[1L]], ]),
              box$name, ggplot_cell_name(box$layout[target$slots[[2L]], ]),
              guide$name)
    bounds <- lapply(cells, function(slot) {
      grob <- guide$grobs[[slot]]
      cell <- c(path, ggplot_cell_name(guide$layout[slot, ]))
      row <- which(drawn$name == grob$name & drawn$gPath == paste(cell, collapse = "::"))
      if (length(row) != 1L) return(NULL)
      viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
      if (length(viewport) < 2L || viewport[[1L]] != "ROOT") return(NULL)
      tryCatch({
        grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
        lower <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
        upper <- grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE)
        bbox <- ggplot_bbox(lower, upper, frame_pt)
        if (all(is.finite(bbox)) && all(bbox[3:4] > 0)) bbox
      }, error = function(error) NULL, finally = grid::upViewport(0))
    })
    if (any(lengths(bounds) != 4L)) next
    boxes <- do.call(rbind, bounds)
    x <- min(boxes[, 1L])
    y <- min(boxes[, 2L])
    result[[gid]] <- c(x, y, max(boxes[, 1L] + boxes[, 3L]) - x,
                       max(boxes[, 2L] + boxes[, 4L]) - y)
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
  if (!all(names(edit) %in% c("vmin", "vmax", "cmap", "orientation", "extend"))) {
    stop("Invalid native R colour scale edit.")
  }
  original_extend <- if (target$can_extend) "neither"
  orientation <- if (is.null(edit$orientation)) target$orientation else edit$orientation
  extend <- if (is.null(edit$extend)) original_extend else edit$extend
  if ((!is.null(edit$orientation) && (is.null(target$orientation) || !is.character(orientation) ||
        length(orientation) != 1L || !orientation %in% ggplot_colourbar_directions)) ||
      (!is.null(edit$extend) && (!target$can_extend || !is.character(extend) ||
        length(extend) != 1L || !extend %in% ggplot_colourbar_extends))) {
    stop("Invalid native R colourbar layout.")
  }
  if (is.null(target$scale) && any(names(edit) %in% c("vmin", "vmax", "cmap"))) {
    stop("The native R colourbar's data mapping cannot be edited.")
  }
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
  list(limits = limits, cmap = name, range_changed = !identical(limits, target$limits),
       orientation = orientation, extend = extend, original_extend = original_extend)
}

ggplot_remap_colour_scales <- function(state, edits, guide_params) {
  built <- state$built
  data <- built$data
  changed <- integer()
  ticks <- list()
  stops <- list()
  directions <- list()
  extensions <- list()
  guide_ticks <- character()
  for (gid in names(edits)) {
    target <- state$colour_scales[[gid]]
    selection <- ggplot_colour_scale_state(target, edits[[gid]], state$palettes)
    if (!identical(selection$orientation, target$orientation)) {
      directions[[target$index]] <- selection$orientation
    }
    if (!is.null(selection$extend) && selection$extend != "neither") {
      extensions[[target$index]] <- selection$extend
    }
    if (!selection$range_changed && is.null(selection$cmap)) next
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
  for (key in names(directions)) guide_params[[key]]$direction <- directions[[key]]
  list(data = data, changed = unique(changed), guides = guide_params, ticks = ticks,
       guide_ticks = guide_ticks, stops = stops, extensions = extensions)
}

ggplot_colour_scale_elements <- function(state, edits, measured, stops) {
  lapply(names(state$colour_scales), function(gid) {
    target <- state$colour_scales[[gid]]
    selection <- ggplot_colour_scale_state(target, edits[[gid]], state$palettes)
    bbox <- if (is.null(measured)) target$bbox else measured[[gid]]
    if (is.null(bbox)) stop("The native R colour bar could not be measured.")
    source <- target$stops
    current <- if (is.null(stops[[gid]])) source else stops[[gid]]
    if (isTRUE(target$reversed)) {
      source <- rev(source)
      current <- rev(current)
    }
    element <- list(gid = gid, role = "colorbar", label = target$title, bbox = as.list(bbox),
         draggable = FALSE, editable = list(), color_scale = list(
           context = target$context, limits = if (!is.null(selection$limits)) as.list(selection$limits),
           original_limits = as.list(target$limits), stops = as.list(source),
           current_stops = as.list(current),
           cmap = selection$cmap, range_editable = target$range_editable,
           orientation = selection$orientation, extend = selection$extend,
           reversed = isTRUE(target$reversed),
           layout_edited = as.list(intersect(names(edits[[gid]]), c("orientation", "extend"))),
           layout_original = list(orientation = target$orientation, extend = selection$original_extend)
         ))
    if (target$host %in% names(state$legend_targets)) element$drag_host <- target$host
    element
  })
}
