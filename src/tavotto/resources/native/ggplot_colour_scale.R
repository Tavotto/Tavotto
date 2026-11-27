# Translate the canonical colorbar (vmin, vmax, cmap) to a trained native
# scale. Capture scalars where ggplot2 maps them, after stats and positions.
# The same replacement scale supplies the plot colors and the guide.

ggplot_observe_colour_maps <- function(plot, capture) {
  original <- plot$scales
  plot$scales <- ggplot_clone(original, non_position_scales = function(self, ...) {
    scales <- ggplot2::ggproto_parent(original, self)$non_position_scales(...)
    ggplot_clone(scales, map_df = function(self, df, ...) {
      mapped <- ggplot2::ggproto_parent(scales, self)$map_df(df, ...)
      channels <- intersect(c("colour", "fill", "shape"), names(df))
      row_keys <- intersect(c("PANEL", "group", "x", "y", "xmin", "xmax", "ymin", "ymax"), names(df))
      capture$maps[[length(capture$maps) + 1L]] <- list(
        input = df[channels], output = mapped[channels], rows = df[row_keys]
      )
      mapped
    })
  })
  plot
}

ggplot_aligned_colour_capture <- function(built, maps, index) {
  if (length(maps) != length(built$data)) return(NULL)
  observed <- maps[[index]]
  data <- built$data[[index]]
  if (identical(observed$rows, data[names(observed$rows)])) observed
}

# Build category membership from the scale's observed input. Different
# levels still own separate elements when their visual styles coincide.
ggplot_discrete_layer_groups <- function(built, mapped, maps, index, aesthetic) {
  if (!aesthetic %in% mapped) return(NULL)
  scale <- built$plot$scales$get_scales(aesthetic)
  if (is.null(scale) || !isTRUE(scale$is_discrete())) return(NULL)
  observed <- ggplot_aligned_colour_capture(built, maps, index)
  values <- observed$input[[aesthetic]]
  data <- built$data[[index]]
  if (!length(values) || !identical(observed$output[[aesthetic]], data[[aesthetic]])) return(NULL)
  keys <- scale$get_breaks()
  labels <- scale$get_labels(keys)
  if (!is.character(labels) || anyNA(labels) || anyDuplicated(labels) ||
      anyDuplicated(keys) || length(keys) != length(labels)) return(NULL)
  membership <- split(match(values, keys), data$PANEL)
  list(keys = keys, labels = labels, membership = membership)
}

ggplot_colour_members <- function(built, maps, channels) {
  if (length(maps) != length(built$data) || !length(maps)) return(NULL)
  members <- list()
  for (i in seq_along(maps)) {
    observed <- ggplot_aligned_colour_capture(built, maps, i)
    if (is.null(observed)) {
      if (length(intersect(channels, names(maps[[i]]$input)))) return(NULL)
      next
    }
    data <- built$data[[i]]
    values <- observed$input[intersect(channels, names(observed$input))]
    if (!length(values) || !nrow(values)) next
    layer <- built$plot$layers[[i]]
    # These stock drawing methods consume final data directly. We can redraw
    # them without invoking a stat, aesthetic expression or custom draw hook.
    if (!class(layer$geom)[[1L]] %in% c("GeomPoint", "GeomTile", "GeomRect", "GeomRaster") ||
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
ggplot_colourbar_tip_fraction <- .05
ggplot_colourbar_outline_keys <- c("outline_visible", "outline_width")

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
      length <- sum(table$widths[bar$l:bar$r]) * ggplot_colourbar_tip_fraction
      table <- gtable::gtable_add_cols(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(1 - tip, 1 - tip, tip), y = c(0, 1, .5), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = bar$t, l = index, b = bar$b, name = name)
    } else {
      index <- if (tip == 1L) bar$t else bar$b + 1L
      length <- sum(table$heights[bar$t:bar$b]) * ggplot_colourbar_tip_fraction
      table <- gtable::gtable_add_rows(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(0, 1, .5), y = c(1 - tip, 1 - tip, tip), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = index, l = bar$l, r = bar$r, name = name)
    }
  }
  table
}

ggplot_colourbar_frame <- function(frame) {
  if (inherits(frame, "zeroGrob")) {
    width <- ggplot2::theme_grey()$rect$linewidth * ggplot2::.pt * ggplot_lwd_pt
    return(list(outline_visible = FALSE, outline_width = width))
  }
  if (inherits(frame, "rect") && is.numeric(frame$gp$lwd) && length(frame$gp$lwd) == 1L &&
      is.finite(frame$gp$lwd) && frame$gp$lwd >= 0) {
    list(outline_visible = TRUE, outline_width = frame$gp$lwd * ggplot_lwd_pt)
  }
}

ggplot_draw_colourbar_frame <- function(table, slot, original, edit, horizontal) {
  visible <- edit$outline_visible %||% original$outline_visible
  if (!visible) {
    table$grobs[[slot]] <- ggplot2::zeroGrob()
    return(table)
  }
  width <- edit$outline_width %||% original$outline_width
  grob <- table$grobs[[slot]]
  if (inherits(grob, "zeroGrob")) {
    grob <- grid::rectGrob(gp = grid::gpar(col = ggplot2::theme_grey()$rect$colour, fill = NA))
  }
  grob$gp$lwd <- width / ggplot_lwd_pt
  tips <- table$layout[startsWith(table$layout$name, ggplot_colourbar_tip_prefix), ]
  if (nrow(tips)) {
    bar <- table$layout[table$layout$name == "bar", ]
    start <- if (horizontal) any(tips$r < bar$l) else any(tips$t > bar$b)
    end <- if (horizontal) any(tips$l > bar$r) else any(tips$b < bar$t)
    fraction <- ggplot_colourbar_tip_fraction
    a <- start * fraction
    b <- a + 1
    along <- c(if (start) 0, a, b, if (end) b + fraction, b, a) / (b + end * fraction)
    across <- c(if (start) .5, 0, 0, if (end) .5, 1, 1)
    grob <- if (horizontal) grid::polygonGrob(x = along, y = across, gp = grob$gp) else
      grid::polygonGrob(x = across, y = along, gp = grob$gp)
    table$layout[slot, c("t", "l", "b", "r")] <- list(
      min(c(bar$t, tips$t)), min(c(bar$l, tips$l)), max(c(bar$b, tips$b)), max(c(bar$r, tips$r))
    )
    # Put one stroke above the extension fills, without drawing seams across
    # the junctions or changing the data strip and its tick positions.
    table$layout$z[slot] <- max(table$layout$z) + 1
  }
  table$grobs[[slot]] <- grob
  table
}

ggplot_capture_colourbar <- function(guide, key, inverse, extend, outline) {
  force(guide)
  force(key)
  force(inverse)
  force(extend)
  force(outline)
  ggplot_clone(guide, assemble_drawing = function(self, grobs, layout, sizes, params, elems) {
    table <- guide$assemble_drawing(grobs, layout, sizes, params, elems)
    endpoints <- ggplot_colourbar_endpoints(params, inverse)
    frame <- which(table$layout$name == "frame")
    original <- if (length(frame) == 1L) ggplot_colourbar_frame(table$grobs[[frame]])
    if (!is.null(extend)) table <- ggplot_extend_colourbar(table, params, extend, endpoints)
    if (!is.null(original) && (length(outline) || (!is.null(extend) && original$outline_visible))) {
      table <- ggplot_draw_colourbar_frame(table, frame, original, outline, params$direction == "horizontal")
    }
    attr(table, "tavotto_colourbar") <- list(
      index = key, orientation = params$direction, can_extend = !is.null(endpoints),
      reversed = if (is.null(endpoints)) isTRUE(params$reverse) else endpoints[["min"]] != 1L,
      outline = original
    )
    table
  })
}

ggplot_colourbar_scale <- function(scales, aesthetics) {
  if (!length(aesthetics) || !all(aesthetics %in% c("colour", "fill"))) return(NULL)
  scale <- scales$get_scales(aesthetics[[1L]])
  if (all(vapply(aesthetics, function(channel) {
    identical(scale, scales$get_scales(channel))
  }, logical(1L)))) scale
}

ggplot_train_colourbar <- function(guide, params, scale, aesthetics) {
  trained <- guide$train(params = params, scale = scale, aesthetic = aesthetics[[1L]])
  if (is.null(trained)) return(NULL)
  for (channel in aesthetics[-1L]) {
    next_params <- guide$train(params = params, scale = scale, aesthetic = channel)
    if (is.null(next_params) || !identical(trained$hash, next_params$hash)) return(NULL)
    merged <- guide$merge(trained, guide, next_params)
    trained <- merged$params
  }
  trained
}

ggplot_colourbar_guides <- function(guides, scales, extensions = NULL, outlines = NULL) {
  actual <- guides$guides
  for (key in names(actual)) {
    guide <- actual[[key]]
    if (identical(class(guide)[[1L]], "GuideColourbar") &&
        identical(as.list(guide)$assemble_drawing, as.list(ggplot2::GuideColourbar)$assemble_drawing)) {
      aesthetic <- guides$params[[key]]$aesthetic
      scale <- ggplot_colourbar_scale(scales, aesthetic)
      if (is.null(scale)) next
      actual[[key]] <- ggplot_capture_colourbar(
        guide, key, scale$get_transformation()$inverse, extensions[[key]], outlines[[key]]
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
    scale <- ggplot_colourbar_scale(built$plot$scales, aesthetic)
    if (is.null(scale) || !identical(class(scale)[[1L]], "ScaleContinuous") ||
        !scale$get_transformation()$name %in% c("identity", "log-10") ||
        !all(scale$aesthetics %in% c("colour", "fill"))) next
    limits <- scale$get_transformation()$inverse(scale$get_limits())
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
      if (!identical(class(guide)[[1L]], "GuideColourbar") ||
          !identical(ggplot_colourbar_scale(built$plot$scales, aesthetic), target$scale)) break
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
    appearance <- list(visible = TRUE)
    title_gid <- paste0(prefix, ".title")
    ticks_gid <- paste0(gid, ".ticks")
    title <- text_targets[[title_gid]]
    ticks <- text_targets[[ticks_gid]]
    if (!is.null(title)) {
      appearance$label <- title$label
    } else if (is.null(params$title) || inherits(params$title, "waiver") ||
               identical(params$title, "")) {
      appearance$label <- ""
      title_gid <- NULL
    }
    if (is.null(ticks)) {
      local <- ggplot_colourbar_ticks(table, item$slots, item$guide, prefix, label)
      ticks <- local[[ticks_gid]]
      ticks_gid <- NULL
    }
    if (!is.null(ticks)) {
      appearance$tick_color <- ticks$color
      size <- ticks$paint$fontsize * ggplot_text_cex(ticks$paint)
      if (is_positive_ggplot_number(size)) appearance$tick_fontsize <- round(size, 2L)
    }
    targets[[gid]] <- c(mapping, list(orientation = meta$orientation, can_extend = meta$can_extend,
                                    reversed = meta$reversed, outline = meta$outline,
                                    appearance = appearance, title_gid = title_gid, ticks_gid = ticks_gid))
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

ggplot_colourbar_appearance <- function(target, edit) {
  appearance <- edit[intersect(names(edit), c("label", "tick_color", "tick_fontsize", "visible"))]
  if (!all(names(appearance) %in% names(target$appearance))) {
    stop("The requested native R colourbar appearance is unavailable.")
  }
  valid <- vapply(names(appearance), function(prop) {
    value <- appearance[[prop]]
    switch(prop,
      label = is.character(value) && length(value) == 1L && !is.na(value),
      tick_color = is.character(value) && length(value) == 1L && !is.na(value) &&
        grepl("^#[0-9A-Fa-f]{6}$", value),
      tick_fontsize = is_positive_ggplot_number(value) && value >= 3 && value <= 200,
      visible = is.logical(value) && length(value) == 1L && !is.na(value)
    )
  }, logical(1L))
  if (!all(valid)) stop("Invalid native R colourbar appearance.")
  appearance
}

ggplot_style_colourbar <- function(params, edit) {
  if (!is.null(edit$label)) params$title <- if (nzchar(edit$label)) edit$label else " "
  if (!is.null(edit$tick_fontsize) || !is.null(edit$tick_color)) {
    text <- ggplot2::element_text(size = edit$tick_fontsize, colour = edit$tick_color)
    patch <- ggplot2::theme(legend.text = text)
    params$theme <- if (is.null(params$theme)) patch else params$theme + patch
  }
  params
}

ggplot_hide_colourbars <- function(table, targets) {
  slots <- unique(vapply(targets, function(target) target$slots[[1L]], integer(1L)))
  blank <- function(grob) grid::nullGrob(name = grob$name)
  for (slot in slots) {
    box <- table$grobs[[slot]]
    guides <- Filter(function(target) target$slots[[1L]] == slot, targets)
    indices <- vapply(guides, function(target) target$slots[[2L]], integer(1L))
    for (index in indices) {
      guide <- box$grobs[[index]]
      guide$grobs <- lapply(guide$grobs, blank)
      box$grobs[[index]] <- guide
    }
    all_guides <- which(box$layout$name == "guides")
    if (setequal(indices, all_guides)) {
      remainder <- setdiff(seq_along(box$grobs), indices)
      box$grobs[remainder] <- lapply(box$grobs[remainder], blank)
    }
    table$grobs[[slot]] <- box
  }
  table
}

ggplot_colour_scale_state <- function(target, edit, catalog) {
  if (!all(names(edit) %in% c("vmin", "vmax", "cmap", "orientation", "extend", ggplot_colourbar_outline_keys,
                           "label", "tick_fontsize", "tick_color", "visible"))) {
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
  outline <- edit[intersect(names(edit), ggplot_colourbar_outline_keys)]
  if (length(outline)) {
    visible <- outline$outline_visible %||% FALSE
    width <- outline$outline_width %||% 0
    if (is.null(target$outline) || !is.logical(visible) || length(visible) != 1L || is.na(visible) ||
        !is.numeric(width) || length(width) != 1L || !is.finite(width) || width < 0 || width > 10) {
      stop("Invalid native R colourbar outline.")
    }
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
    if (identical(target$scale$get_transformation()$name, "log-10") && limits[[1L]] <= 0) {
      stop("A logarithmic native R colour scale needs positive colour limits.")
    }
  }
  name <- edit$cmap
  if (!is.null(name) && (!is.character(name) || length(name) != 1L ||
                        !name %in% names(catalog$palettes))) stop("Unknown Tavotto palette.")
  list(limits = limits, cmap = name, range_changed = !identical(limits, target$limits),
       orientation = orientation, extend = extend, original_extend = original_extend, outline = outline,
       appearance = ggplot_colourbar_appearance(target, edit))
}

ggplot_remap_colour_scales <- function(state, edits, guide_params) {
  built <- state$built
  data <- built$data
  changed <- integer()
  ticks <- list()
  stops <- list()
  directions <- list()
  extensions <- list()
  outlines <- list()
  appearances <- list()
  names_changed <- list()
  hidden <- list()
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
    if (length(selection$outline)) outlines[[target$index]] <- selection$outline
    if (length(selection$appearance)) appearances[[target$index]] <- selection$appearance
    if (!is.null(target$title_gid) && !is.null(selection$appearance$label)) {
      names_changed[[target$title_gid]] <- if (nzchar(selection$appearance$label)) selection$appearance$label else " "
    }
    if (identical(selection$appearance$visible, FALSE)) hidden[[gid]] <- target
    if (!selection$range_changed && is.null(selection$cmap)) next
    packed <- if (!is.null(selection$cmap)) state$palettes$palettes[[selection$cmap]]
    scale <- ggplot_set_colour_mapping(
      ggplot_clone(target$scale), if (selection$range_changed) selection$limits, packed
    )
    for (key in names(target$guides)) {
      guide <- built$plot$guides$guides[[key]]
      descriptor <- target$guides[[key]]
      params <- ggplot_train_colourbar(guide, guide_params[[key]], scale, descriptor$aesthetic)
      if (is.null(params)) stop("The native R colour bar could not be drawn for this range.")
      guide_params[[key]] <- params
      if (!is.null(descriptor$ticks)) {
        tick_gid <- descriptor$ticks
        guide_ticks <- c(guide_ticks, tick_gid)
        labels <- list(tick_labels = as.list(params$key$.label))
        ticks[[tick_gid]] <- ggplot_numeric_key(params$key, labels, ggplot_colourbar_key_values(params, scale))
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
  for (key in names(appearances)) guide_params[[key]] <- ggplot_style_colourbar(guide_params[[key]], appearances[[key]])
  list(data = data, changed = unique(changed), guides = guide_params, ticks = ticks,
       guide_ticks = guide_ticks, stops = stops, extensions = extensions, outlines = outlines,
       title_changes = names_changed, hidden = hidden)
}

ggplot_colour_source_anchors <- function(targets) {
  anchors <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    if (!length(target$guides)) next
    for (guide in target$guides) {
      if (is.null(guide$ticks)) next
      bar <- sub("\\.ticks$", "", guide$ticks)
      anchor <- rlang::hash(list(
        bar, targets[[bar]]$title, sort(guide$aesthetic), target$scale$aesthetics, target$members
      ))
      anchors[[bar]] <- anchor
      anchors[[guide$ticks]] <- anchor
    }
  }
  anchors
}

ggplot_colour_scale_elements <- function(state, edits, measured, stops, anchors) {
  lapply(names(state$colour_scales), function(gid) {
    target <- state$colour_scales[[gid]]
    selection <- ggplot_colour_scale_state(target, edits[[gid]], state$palettes)
    appearance <- utils::modifyList(target$appearance %||% list(), selection$appearance %||% list())
    narrow_color <- if (!is.null(target$ticks_gid)) edits[[target$ticks_gid]]$color
    if (!is.null(narrow_color) && "tick_color" %in% names(appearance)) {
      appearance$tick_color <- narrow_color
    }
    bbox <- if (identical(appearance$visible, FALSE)) c(-1, -1, 0, 0) else {
      if (is.null(measured)) target$bbox else measured[[gid]]
    }
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
           layout_original = list(orientation = target$orientation, extend = selection$original_extend),
           appearance = list(original = target$appearance, current = appearance, ticks_gid = target$ticks_gid),
           outline = if (!is.null(target$outline)) {
             original <- target$outline
             original$outline_width <- round(original$outline_width, 2L)
             list(original = original, edited = selection$outline)
           }
         ))
    element$colour_source_anchor <- anchors[[gid]]
    if (selection$range_changed || !is.null(selection$cmap)) {
      writes <- if (selection$range_changed) c("vmin", "vmax") else character()
      element$source_colour_mapping <- list(
        aesthetics = as.list(target$scale$aesthetics),
        limits = if (selection$range_changed) as.list(selection$limits),
        palette = selection$cmap,
        edited = edits[[gid]][intersect(names(edits[[gid]]), c(writes, "cmap"))]
      )
    }
    if (target$host %in% names(state$legend_targets)) element$drag_host <- target$host
    element
  })
}
