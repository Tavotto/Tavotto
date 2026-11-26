# The adapter receives a plot from an already authorized R invocation. Building
# the layout and applying edits are separate so a paint change never reruns a stat.

with_svg_frame <- function(frame_pt, draw) {
  if (!is.numeric(frame_pt) || length(frame_pt) != 2L ||
      any(!is.finite(frame_pt)) || any(frame_pt <= 0)) {
    stop("The SVG frame needs two positive dimensions in points.")
  }
  capture <- svglite::svgstring(width = frame_pt[[1L]] / 72,
                               height = frame_pt[[2L]] / 72)
  device <- grDevices::dev.cur()
  on.exit({
    if (device %in% grDevices::dev.list()) grDevices::dev.off(which = device)
  })
  value <- draw(capture)
  grDevices::dev.off(which = device)
  list(value = value, svg = as.character(capture()))
}

ggplot_title_fields <- rbind(
  "plot.title" = c("title", "plot.title"),
  "plot.subtitle" = c("subtitle", "plot.subtitle"),
  "plot.caption" = c("caption", "plot.caption"),
  "axis.x.bottom.title" = c("xlab-b", "axis.title.x.bottom"),
  "axis.x.top.title" = c("xlab-t", "axis.title.x.top"),
  "axis.y.left.title" = c("ylab-l", "axis.title.y.left"),
  "axis.y.right.title" = c("ylab-r", "axis.title.y.right")
)
ggplot_text_slots <- ggplot_title_fields[, 1L]
ggplot_title_theme_names <- ggplot_title_fields[, 2L]

# A source edit and a canvas edit address the same four tick-label groups.
ggplot_axis_tick_fields <- rbind(
  "axis.x.bottom.ticks" = c("axis-b", "\u4e0b", "axis.text.x.bottom"),
  "axis.x.top.ticks" = c("axis-t", "\u4e0a", "axis.text.x.top"),
  "axis.y.left.ticks" = c("axis-l", "\u5de6", "axis.text.y.left"),
  "axis.y.right.ticks" = c("axis-r", "\u53f3", "axis.text.y.right")
)
ggplot_axis_tick_theme_names <- ggplot_axis_tick_fields[, 3L]

ggplot_cell_name <- function(cell) {
  paste0(cell$name, ".", paste(c(cell$t, cell$r, cell$b, cell$l), collapse = "-"))
}

ggplot_text_target <- function(table, slots, role, title_child = NULL, group_label = NULL) {
  path <- character()
  for (slot in slots) {
    if (is.character(slot)) {
      path <- c(path, table$name)
      table <- table$children[[slot]]
      next
    }
    # gtable names its drawn cells in top-right-bottom-left order.
    cell <- ggplot_cell_name(table$layout[slot, ])
    path <- c(path, table$name, cell)
    table <- table$grobs[[slot]]
  }
  if (!is.null(title_child)) {
    path <- c(path, table$name)
    table <- table$children[[title_child]]
  }
  children <- table$children
  if (length(children) != 1L) return(NULL)
  text <- children[[1L]]
  if (!inherits(text, "text")) return(NULL)
  label <- text$label
  if (is.null(group_label)) {
    if (!is.character(label) || length(label) != 1L || is.na(label) || !nzchar(label)) return(NULL)
  } else {
    if ((!is.character(label) && !is.numeric(label)) || !length(label) ||
        anyNA(label) || any(!nzchar(label)) || isTRUE(text$check.overlap)) return(NULL)
  }
  # Resolve R color names (such as grey42) for the canvas color picker.
  rgb <- tryCatch(grDevices::col2rgb(text$gp$col, alpha = TRUE),
                  error = function(error) NULL)
  if (is.null(rgb) || ncol(rgb) != 1L) return(NULL)
  color <- grDevices::rgb(rgb[1L], rgb[2L], rgb[3L],
                          alpha = if (rgb[4L] != 255L) rgb[4L] else NULL,
                          maxColorValue = 255)
  font <- text$gp$font
  face <- NULL
  if (is.null(group_label) && length(font) == 1L && font %in% 1:4) {
    face <- list(weight = if (font %in% c(2L, 4L)) "bold" else "normal",
                 style = if (font %in% c(3L, 4L)) "italic" else "normal")
  }
  edit_path <- if (is.null(title_child)) text$name else grid::gPath(table$name, text$name)
  list(slots = slots, title_child = title_child,
       parent_path = paste(c(path, table$name), collapse = "::"),
       child = text$name, edit_path = edit_path,
       label = if (is.null(group_label)) label else group_label,
       tick_labels = if (!is.null(group_label)) as.list(as.character(label)) else NULL,
       paint = text$gp, color = color, face = face, role = role)
}

ggplot_axis_ticks <- function(table) {
  # A panel can repeat guides within a facet. Start with the four unique outer
  # cells of a single-panel plot, even when a cell contains a secondary axis.
  if (length(which(grepl("^panel(-[0-9]+-[0-9]+)?$", table$layout$name))) != 1L) return(list())
  targets <- list()
  for (gid in rownames(ggplot_axis_tick_fields)) {
    side <- ggplot_axis_tick_fields[gid, ]
    slot <- which(table$layout$name == side[[1L]])
    if (length(slot) != 1L) next
    axis <- table$grobs[[slot]]$children[["axis"]]
    if (!inherits(axis, "gtable")) next
    labels <- which(vapply(axis$grobs, inherits, logical(1L), "titleGrob"))
    if (length(labels) != 1L) next
    label <- paste0(side[[2L]], "\u8f74\u523b\u5ea6\u6587\u5b57")
    targets[[gid]] <- ggplot_text_target(table, list(slot, "axis", labels), "ticks",
                                         group_label = label)
  }
  targets
}

ggplot_legend_entries <- function(table, slots, guide, prefix, title) {
  cells <- guide$layout$name
  labels <- which(grepl("^label-[0-9]+-[0-9]+$", cells))
  keys <- cells[grepl("^key-[0-9]+-[0-9]+-bg$", cells)]
  # A standard keyed legend has one cell per key and one per label.
  if (!length(labels) || length(labels) != length(keys) ||
      anyDuplicated(keys) || anyDuplicated(cells[labels])) return(list())
  targets <- list()
  for (slot in labels) {
    target <- ggplot_text_target(table, c(slots, slot), "legend_text")
    if (is.null(target)) next
    cell <- sub("^label-", "", cells[[slot]])
    gid <- paste0(prefix, ".entry.", gsub("-", ".", cell, fixed = TRUE))
    target$legend_title <- title
    targets[[gid]] <- target
  }
  text <- vapply(targets, function(target) target$label, character(1L))
  targets[!(duplicated(text) | duplicated(text, fromLast = TRUE))]
}

ggplot_colourbar_titles <- function(built) {
  # Colour steps can draw the same cells. Confirm the built guide class and
  # join to the rendered guide only by a unique title.
  guides <- built$plot$guides
  if (!length(guides$guides) || !identical(names(guides$guides), names(guides$params))) {
    return(character())
  }
  titles <- vapply(guides$params, function(params) {
    title <- params$title
    if (is.character(title) && length(title) == 1L && !is.na(title)) title else ""
  }, character(1L))
  colourbars <- vapply(guides$guides, function(guide) {
    identical(class(guide)[[1L]], "GuideColourbar")
  }, logical(1L))
  titles[colourbars & nzchar(titles) & !duplicated(titles) & !duplicated(titles, fromLast = TRUE)]
}

# Formatting uses the trained guide values, never the rounded screen labels
# as input. Only expose it when the rendered labels prove a one-to-one match
# with plain numerical values. Custom labels, dates and transformed values
# that differ from the guide's values keep their script formatting.
ggplot_major_axis_key <- function(key) {
  if (".type" %in% names(key)) key[key$.type == "major", , drop = FALSE] else key
}

ggplot_numeric_key <- function(key, target, values = key$.value) {
  if (!is.data.frame(key) || !is.numeric(values) || !is.character(key$.label) ||
      any(!is.finite(values)) || anyNA(key$.label) ||
      !identical(as.list(key$.label), target$tick_labels)) return(NULL)
  labels <- sub("^\u2212", "-", key$.label)
  number <- "^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"
  if (!all(grepl(number, labels))) return(NULL)
  numeric_labels <- as.numeric(labels)
  device_rounding <- 32 * .Machine$double.eps * max(abs(values))
  if (length(numeric_labels) == length(values) && all(is.finite(numeric_labels)) &&
      all(abs(numeric_labels - values) <=
          device_rounding + 1e-12 * pmax(abs(numeric_labels), abs(values)))) {
    return(numeric_labels)
  }
  NULL
}

ggplot_colourbar_key_values <- function(params) {
  # GuideColourbar stores tick positions in bar coordinates after training.
  # Recover scale values from the same endpoints. The caller also checks the
  # result against every actually drawn label before exposing the editor.
  range <- params$decor$value
  if (!is.numeric(range) || length(range) < 2L || !all(is.finite(range)) ||
      !is.numeric(params$key$.value) || length(params$display) != 1L ||
      !params$display %in% c("gradient", "raster", "rectangles")) {
    return(NULL)
  }
  inset <- 0
  if (params$display != "gradient") {
    if (!is_positive_ggplot_number(params$nbin) || params$nbin <= 1) return(NULL)
    inset <- 0.5 / params$nbin
  }
  endpoints <- range[c(1L, length(range))]
  endpoints[[1L]] + (params$key$.value - inset) / (1 - 2 * inset) * diff(endpoints)
}

ggplot_axis_spacing <- function(built, panel, params, values) {
  axis <- params$aesthetic
  if (!identical(class(built$layout$coord)[[1L]], "CoordCartesian") ||
      length(axis) != 1L || !axis %in% c("x", "y") || !length(values) ||
      !identical(as.numeric(params$n.dodge), 1)) return(NULL)
  view <- panel[[axis]]
  scale <- view$scale
  if (!identical(class(view)[[1L]], "ViewScale") || isTRUE(view$scale_is_discrete) ||
      !identical(class(scale)[[1L]], "ScaleContinuousPosition") ||
      !scale$get_transformation()$name %in% names(ggplot_axis_modes) ||
      !inherits(scale$secondary.axis, "waiver") || !inherits(scale$labels, "waiver") ||
      (!is.null(scale$minor_breaks) && !inherits(scale$minor_breaks, "waiver"))) return(NULL)
  cross <- if (axis == "x") "y" else "x"
  key <- ggplot_major_axis_key(params$key)
  if (!setequal(setdiff(names(key), ".type"), c(axis, cross, ".label", ".value")) ||
      length(unique(key[[cross]])) != 1L ||
      !isTRUE(all.equal(key[[axis]], ggplot_axis_position(panel, axis, key$.value), tolerance = 1e-12))) {
    return(NULL)
  }
  if (is.null(ggplot_axis_pair(sort(view$continuous_range)))) return(NULL)
  step <- if (identical(scale$get_transformation()$name, "identity") && length(values) >= 2L) {
    stats::median(diff(sort(values)))
  }
  minor <- ggplot_minor_tick_defaults(built, params, panel, axis)
  list(axis = axis, step = if (is_positive_ggplot_number(step)) step, minor = minor)
}

ggplot_axis_key_values <- function(panel, params) {
  key <- ggplot_major_axis_key(params$key)
  if (!length(key$.value)) return(numeric())
  transform <- panel[[params$aesthetic]]$scale$get_transformation()
  transform$inverse(key$.value)
}

ggplot_tick_targets <- function(built, targets, colourbar_titles) {
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    if (is.null(target$tick_labels)) next
    if (target$role == "ticks" && length(built$layout$panel_params) == 1L) {
      position <- strsplit(gid, ".", fixed = TRUE)[[1L]][[3L]]
      panel <- built$layout$panel_params[[1L]]
      guides <- panel$guides
      matches <- which(vapply(guides$params, function(p) identical(p$position, position), logical(1L)))
      if (length(matches) == 1L && identical(class(guides$guides[[matches]])[[1L]], "GuideAxis")) {
        params <- guides$params[[matches]]
        key <- ggplot_major_axis_key(params$key)
        values <- ggplot_numeric_key(key, target)
        if (is.null(values) && identical(params$aesthetic, substr(gid, 6L, 6L)) &&
            isTRUE(panel[[params$aesthetic]]$scale$get_transformation()$name %in%
                   setdiff(names(ggplot_axis_modes), "identity"))) {
          values <- ggplot_numeric_key(key, target, ggplot_axis_key_values(panel, params))
        }
        if (!is.null(values)) {
          size <- target$paint$fontsize * ggplot_text_cex(target$paint)
          result[[gid]] <- list(kind = "axis", index = matches, values = values,
                                spacing = ggplot_axis_spacing(built, panel, params, values),
                                size = if (is_positive_ggplot_number(size)) size)
        }
      }
    } else if (target$role == "legend_text" && target$legend_title %in% colourbar_titles) {
      index <- match(target$legend_title, unname(colourbar_titles))
      key <- names(colourbar_titles)[[index]]
      params <- built$plot$guides$params[[key]]
      values <- ggplot_numeric_key(params$key, target, ggplot_colourbar_key_values(params))
      if (!is.null(values)) {
        result[[gid]] <- list(kind = "colourbar", index = key, values = values)
      }
    }
  }
  result
}

ggplot_number_formats <- c("auto", "%.0f", "%.1f", "%.2f", "%.3f", "%g", "sci")
ggplot_major_tick_props <- c("major_mode", "major_step", "major_values")
ggplot_minor_tick_props <- c("minor_visible", "minor_mode", "minor_step")
ggplot_tick_props <- c("format", ggplot_major_tick_props, ggplot_minor_tick_props)

ggplot_format_guide <- function(params, format, values) {
  if (!is.character(format) || length(format) != 1L || !format %in% ggplot_number_formats) {
    stop("Invalid native R tick number format.")
  }
  if (format != "auto") {
    labels <- if (format == "sci") {
      sprintf("%.2e", values)
    } else {
      sprintf(format, values)
    }
    if (".type" %in% names(params$key)) {
      params$key$.label[params$key$.type == "major"] <- labels
    } else params$key$.label <- labels
  }
  params
}

ggplot_watch_layer <- function(layer, index, captured) {
  force(layer)
  force(index)
  ggplot2::ggproto(NULL, layer, draw_geom = function(data, layout) {
    grobs <- layer$draw_geom(data, layout)
    captured$grobs[[index]] <- grobs
    grobs
  })
}

ggplot_frozen_layer <- function(layer, grobs) {
  force(grobs)
  ggplot2::ggproto(NULL, layer, draw_geom = function(data, layout) grobs)
}

ggplot_clone <- function(parent, ...) {
  force(parent)
  ggplot2::ggproto(NULL, parent, ...)
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

ggplot_major_tick_modes <- function(spacing) {
  c("auto", if (!is.null(spacing$step)) "step", "fixed")
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

ggplot_edit_axis_guide <- function(panel, params, target, edit, coord, projected = FALSE) {
  values <- if (projected) ggplot_axis_key_values(panel, params) else target$values
  format <- if (is.null(edit$format)) "auto" else edit$format
  spacing <- target$spacing
  replaced_breaks <- FALSE
  if (any(ggplot_major_tick_props %in% names(edit))) {
    if (is.null(spacing)) stop("The requested native R tick spacing is unavailable.")
    mode <- ggplot_major_tick_mode(edit)
    step <- if (is.null(edit$major_step)) spacing$step else edit$major_step
    fixed <- if (!is.null(edit$major_values)) ggplot_fixed_tick_values(edit$major_values)
    if (!is.character(mode) || length(mode) != 1L || !mode %in% ggplot_major_tick_modes(spacing) ||
        (!is.null(edit$major_step) && !is_positive_ggplot_number(step))) {
      stop("Invalid native R tick spacing.")
    }
    view <- panel[[spacing$axis]]
    transform <- view$scale$get_transformation()
    transformed_range <- sort(view$continuous_range)
    supplied <- length(fixed) && mode == "fixed"
    if (mode == "step") {
      values <- ggplot_uniform_breaks(transform$inverse(transformed_range), step)
      breaks <- transform$transform(values)
    } else if (supplied) {
      # Crop ticks to the visible view without expanding or retraining the axis.
      # Compare in scale coordinates so the guide and grid share the same marks.
      if (identical(transform$name, "log-10")) fixed <- fixed[fixed > 0]
      if (identical(transform$name, "prob-logis")) fixed <- fixed[fixed > 0 & fixed < 1]
      values <- sort(unique(fixed))
      breaks <- transform$transform(values)
      visible <- breaks >= transformed_range[[1L]] & breaks <= transformed_range[[2L]]
      values <- values[visible]
      breaks <- breaks[visible]
    }
    if (mode == "step" || supplied) {
      minor <- if (length(breaks) >= 2L) {
        view$scale$get_breaks_minor(b = breaks, limits = transformed_range)
      }
      panel[[spacing$axis]] <- ggplot_clone(view, breaks = breaks, minor_breaks = minor)
      major <- ggplot_major_axis_key(params$key)
      key <- major[rep(1L, length(values)), , drop = FALSE]
      key[[spacing$axis]] <- ggplot_axis_position(panel, spacing$axis, breaks)
      key$.value <- breaks
      key$.label <- if (length(breaks)) view$scale$get_labels(breaks) else character()
      params$key <- key
      replaced_breaks <- TRUE
    }
  }
  minor <- edit[intersect(names(edit), ggplot_minor_tick_props)]
  if (length(minor) || (replaced_breaks && isTRUE(params$minor.ticks))) {
    revised <- ggplot_edit_minor_ticks(panel, params, spacing, minor, coord)
    panel <- revised$panel
    params <- revised$params
  }
  list(panel = panel, params = ggplot_format_guide(params, format, values))
}

ggplot_guide_table <- function(state, edits, colour_edits, axis_sizes, guide_strokes, axis_changes,
                               grid_edits, title_theme) {
  built <- state$built
  plot <- built$plot
  if (length(grid_edits)) plot$theme <- ggplot_grid_theme(plot$theme, state$axes_target$grid, grid_edits)
  if (length(title_theme)) plot$theme <- plot$theme + do.call(ggplot2::theme, title_theme)
  guide_params <- plot$guides$params
  remapped <- if (length(colour_edits)) ggplot_remap_colour_scales(state, colour_edits, guide_params)
  if (!is.null(remapped)) guide_params <- remapped$guides
  if (!is.null(remapped)) built$data <- remapped$data
  if (length(axis_changes)) built <- ggplot_project_axes(built, state$axes_target, axis_changes)
  layout <- ggplot_clone(built$layout)
  panels <- layout$panel_params
  for (gid in names(guide_strokes)) {
    target <- state$line_targets[[gid]]
    style <- ggplot_line_style(target, guide_strokes[[gid]])
    for (index in target$guide_keys) {
      key <- guide_params[[index]]$decor[[target$key_name]]$data
      if (!is.null(style$lwd)) key$linewidth <- style$lwd / ggplot2::.pt
      if (!is.null(style$lty)) key$linetype <- style$lty
      guide_params[[index]]$decor[[target$key_name]]$data <- key
    }
  }
  if (length(panels) == 1L) axis_params <- panels[[1L]]$guides$params
  hidden_ticks <- character()
  for (gid in names(edits)) {
    target <- state$tick_targets[[gid]]
    if (is.null(target)) stop("The requested native R tick edit is unavailable.")
    index <- target$index
    edit <- edits[[gid]]
    if (target$kind == "axis") {
      params <- axis_params[[index]]
      edited <- ggplot_edit_axis_guide(panels[[1L]], params, target, edit, layout$coord,
                                      projected = params$aesthetic %in% names(axis_changes))
      panels[[1L]] <- edited$panel
      axis_params[[index]] <- edited$params
      if (is.data.frame(edited$params$key) && !nrow(ggplot_major_axis_key(edited$params$key))) {
        hidden_ticks <- c(hidden_ticks, gid)
      }
    } else {
      if (!identical(names(edit), "format")) stop("The requested native R colourbar edit is unavailable.")
      values <- remapped$ticks[[gid]]
      if (is.null(values)) {
        if (gid %in% remapped$guide_ticks && edit$format != "auto") {
          stop("The native R range could not retain the requested tick number format.")
        }
        values <- target$values
      }
      guide_params[[index]] <- ggplot_format_guide(guide_params[[index]], edit$format, values)
    }
  }
  for (gid in names(axis_sizes)) {
    size <- axis_sizes[[gid]]
    if (!is_positive_ggplot_number(size) || size < 3 || size > 200) {
      stop("The native R tick font size must be between 3 and 200 points.")
    }
    index <- state$tick_targets[[gid]]$index
    position <- strsplit(gid, ".", fixed = TRUE)[[1L]][2:3]
    element <- paste(c("axis.text", position), collapse = ".")
    patch <- do.call(ggplot2::theme, stats::setNames(list(ggplot2::element_text(size = size)), element))
    theme <- axis_params[[index]]$theme
    axis_params[[index]]$theme <- if (is.null(theme)) patch else theme + patch
  }
  if (length(panels) == 1L) {
    panels[[1L]]$guides <- ggplot_clone(panels[[1L]]$guides, params = axis_params)
    layout$panel_params <- panels
  }
  plot$guides <- ggplot_colourbar_guides(
    ggplot_clone(plot$guides, params = guide_params), plot$scales, remapped$extensions
  )
  captured <- new.env(parent = emptyenv())
  if (length(axis_changes)) captured$grobs <- vector("list", length(plot$layers))
  plot$layers <- Map(function(layer, grobs, index) {
    if (length(axis_changes)) {
      ggplot_watch_layer(ggplot_mirror_raster_layer(layer, layout$coord), index, captured)
    } else if (index %in% remapped$changed) {
      ggplot_mirror_raster_layer(layer, layout$coord)
    } else ggplot_frozen_layer(layer, grobs)
  }, plot$layers, state$layer_grobs, seq_along(plot$layers))
  built$layout <- layout
  built$plot <- plot
  axes <- if (length(axis_changes)) ggplot_axes_values(layout, state$axes_target, axis_changes)
  tick_values <- lapply(Filter(function(tick) !is.null(tick$spacing), state$tick_targets), function(tick) {
    ggplot_axis_key_values(panels[[1L]], axis_params[[tick$index]])
  })
  list(table = ggplot2::ggplot_gtable(built), layout = layout, stops = remapped$stops,
       guide_ticks = remapped$guide_ticks, axes = axes, layer_grobs = captured$grobs,
       tick_values = tick_values, hidden_ticks = hidden_ticks)
}

ggplot_colourbar_ticks <- function(table, slots, guide, prefix, title) {
  cells <- guide$layout$name
  labels <- which(grepl("^label-[0-9]+-[0-9]+$", cells))
  if (sum(cells == "bar") != 1L || length(labels) != 1L) return(list())
  label <- paste0("\u8272\u6761\u523b\u5ea6 \u201c", title, "\u201d")
  target <- ggplot_text_target(table, c(slots, labels), "legend_text", group_label = label)
  if (is.null(target)) return(list())
  target$legend_title <- title
  stats::setNames(list(target), paste0(prefix, ".colourbar.ticks"))
}

ggplot_legend_guides <- function(table) {
  result <- list()
  for (box_slot in which(startsWith(table$layout$name, "guide-box-"))) {
    box <- table$grobs[[box_slot]]
    if (!inherits(box, "gtable")) next
    position <- sub("^guide-box-", "", table$layout$name[[box_slot]])
    guides <- which(box$layout$name == "guides")
    for (index in seq_along(guides)) {
      guide_slot <- guides[[index]]
      guide <- box$grobs[[guide_slot]]
      if (!inherits(guide, "gtable")) next
      prefix <- paste("legend", position, index, sep = ".")
      result[[prefix]] <- list(guide = guide, slots = c(box_slot, guide_slot),
                               host = paste("legend", position, sep = "."))
    }
  }
  result
}

ggplot_legend_targets <- function(table, colourbar_titles) {
  titles <- list()
  guide_labels <- list()
  guides <- ggplot_legend_guides(table)
  for (prefix in names(guides)) {
    item <- guides[[prefix]]
    guide <- item$guide
    slots <- item$slots
    title_slot <- which(guide$layout$name == "title")
    if (length(title_slot) != 1L) next
    target <- ggplot_text_target(table, c(slots, title_slot), "legend_text")
    if (is.null(target)) next
    gid <- paste0(prefix, ".title")
    titles[[gid]] <- target
    guide_labels[[gid]] <- if (target$label %in% colourbar_titles) {
      ggplot_colourbar_ticks(table, slots, guide, prefix, target$label)
    } else {
      ggplot_legend_entries(table, slots, guide, prefix, target$label)
    }
  }
  # An index and a label cannot distinguish same-named guides after reordering.
  labels <- vapply(titles, function(target) target$label, character(1L))
  titles <- titles[!(duplicated(labels) | duplicated(labels, fromLast = TRUE))]
  c(titles, unlist(unname(guide_labels[names(titles)]), recursive = FALSE))
}

# A ggplot guide box can contain several guides and a shared background. Move
# the box as drawn, preserving its layout space, contents and order.
ggplot_legend_boxes <- function(table) {
  if (!"over" %in% grDevices::dev.capabilities()$compositing) return(list())
  targets <- list()
  for (side in c("right", "left", "top", "bottom", "inside")) {
    slot <- which(table$layout$name == paste0("guide-box-", side))
    if (length(slot) != 1L) next
    cell <- table$layout[slot, ]
    box <- table$grobs[[slot]]
    if (!inherits(box, "gtable") || !inherits(box$vp, "viewport") ||
        box$vp$angle != 0 || cell$clip != "off") next
    guides <- box$grobs[box$layout$name == "guides"]
    if (!length(guides) || !all(vapply(guides, inherits, logical(1L), "gtable"))) next
    context <- lapply(guides, function(guide) {
      cells <- guide$layout$name
      labels <- guide$grobs[cells == "title" | startsWith(cells, "label-")]
      list(cells = as.list(cells), labels = lapply(labels, function(grob) {
        lapply(grob$children, function(child) as.list(as.character(child$label)))
      }))
    })
    targets[[paste0("legend.", side)]] <- list(
      slot = slot, viewport = c(table$name, ggplot_cell_name(cell), box$vp$name),
      context = context
    )
  }
  targets
}

measure_ggplot_legends <- function(targets, frame_pt) {
  if (!length(targets)) return(targets)
  forced <- tryCatch({
    grid::grid.force()
    TRUE
  }, error = function(error) FALSE)
  if (!forced) return(lapply(targets, function(target) NULL))
  lapply(targets, function(target) {
    tryCatch({
      on.exit(grid::upViewport(0))
      grid::downViewport(do.call(grid::vpPath, as.list(target$viewport)), strict = TRUE)
      lower <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
      upper <- grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE)
      bbox <- ggplot_bbox(lower, upper, frame_pt)
      if (all(is.finite(bbox)) && all(bbox[3:4] > 0)) bbox else NULL
    }, error = function(error) NULL)
  })
}

ggplot_facet_labels <- function(table) {
  targets <- list()
  keys <- character()
  cells <- table$layout$name
  slots <- which(grepl("^strip-[tblr]-[0-9]+(-[0-9]+)?$", cells) &
                   !duplicated(cells) & !duplicated(cells, fromLast = TRUE))
  for (slot in slots) {
    strip <- table$grobs[[slot]]
    if (!inherits(strip, "gtable") || length(strip$grobs) != 1L ||
        !identical(strip$layout$name, "strip")) next
    titles <- which(vapply(strip$grobs[[1L]]$children, inherits, logical(1L), "titleGrob"))
    if (length(titles) != 1L) next
    target <- ggplot_text_target(table, c(slot, 1L), "text", title_child = titles[[1L]])
    if (is.null(target)) next
    position <- sub("^strip-", "", cells[[slot]])
    gid <- paste0("facet.", gsub("-", ".", position, fixed = TRUE), ".label")
    targets[[gid]] <- target
    keys <- c(keys, paste(substr(position, 1L, 1L), target$label, sep = ":"))
  }
  # Reordered strips with duplicate labels on the same side have no stable target.
  targets[!(duplicated(keys) | duplicated(keys, fromLast = TRUE))]
}

measure_ggplot_text <- function(drawn, target, frame_pt) {
  row <- which(drawn$name == target$child & drawn$gPath == target$parent_path)
  if (length(row) != 1L) return(NULL)
  # Nested guides can insert viewports that are not part of their grob paths.
  viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
  if (length(viewport) < 2L || viewport[[1L]] != "ROOT") return(NULL)
  on.exit(grid::upViewport(0))
  grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
  text <- grid::grid.get(grid::gPath(target$parent_path, target$child), strict = TRUE)
  lower <- grid::deviceLoc(grid::grobX(text, 180), grid::grobY(text, 270), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::grobX(text, 0), grid::grobY(text, 90), valueOnly = TRUE)
  ggplot_bbox(lower, upper, frame_pt)
}

ggplot_bbox <- function(lower, upper, frame_pt) {
  frame_in <- frame_pt / 72
  c(lower$x / frame_in[[1L]], 1 - upper$y / frame_in[[2L]],
    (upper$x - lower$x) / frame_in[[1L]], (upper$y - lower$y) / frame_in[[2L]])
}

ggplot_text_targets <- function(table, frame_pt, colourbar_titles) {
  targets <- list()
  families <- ggplot_installed_title_fonts()
  for (gid in names(ggplot_text_slots)) {
    slot <- which(table$layout$name == ggplot_text_slots[[gid]])
    if (length(slot) != 1L) next
    role <- if (gid == "plot.title") "title" else if (startsWith(gid, "axis.")) "axis_label" else "text"
    target <- ggplot_text_target(table, slot, role)
    if (is.null(target)) next
    if (inherits(table$grobs[[slot]], "titleGrob") &&
        is_positive_ggplot_number(target$paint$fontsize) &&
        is_positive_ggplot_number(ggplot_text_cex(target$paint))) {
      target$title_dimension <- if (startsWith(gid, "axis.y.")) "width" else "height"
      target$title_fonts <- families
    }
    targets[[gid]] <- target
  }
  targets <- c(targets, ggplot_legend_targets(table, colourbar_titles), ggplot_facet_labels(table),
               ggplot_axis_ticks(table))
  measure_ggplot_targets(targets, frame_pt)
}

measure_ggplot_targets <- function(targets, frame_pt) {
  if (!length(targets)) return(targets)
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  for (gid in names(targets)) {
    if (isTRUE(targets[[gid]]$hidden)) {
      targets[[gid]]$bbox <- c(-1, -1, 0, 0)
      next
    }
    bbox <- tryCatch(measure_ggplot_text(drawn, targets[[gid]], frame_pt),
                     error = function(error) NULL)
    if (length(bbox) == 4L && all(is.finite(bbox)) && all(bbox[3:4] > 0)) {
      targets[[gid]]$bbox <- bbox
    } else {
      targets[[gid]] <- NULL
    }
  }
  targets
}

ggplot_line_types <- c("-" = "solid", "--" = "dashed", ":" = "dotted", "-." = "dotdash")
ggplot_lwd_pt <- 72 / 96

ggplot_uniform_pigment <- function(colour) {
  if (is.logical(colour) && length(colour) && all(is.na(colour))) {
    return(list(rgb = "none", alpha = 0))
  }
  if (!is.character(colour) || !length(colour)) return(NULL) # grid pattern or gradient
  colours <- unique(colour)
  rgba <- grDevices::col2rgb(colours, alpha = TRUE)
  if (!all(rgba == rgba[, 1L])) return(NULL)
  alpha <- rgba[4L, 1L] / 255
  list(rgb = if (alpha == 0) "none" else grDevices::rgb(rgba[1L, 1L], rgba[2L, 1L],
                                                       rgba[3L, 1L], maxColorValue = 255),
       alpha = alpha)
}

ggplot_edit_pigment <- function(value, original, alpha, layer_alpha) {
  if (value == "none") return(NA_character_)
  if (is.null(alpha)) alpha <- if (original$alpha > 0) original$alpha else layer_alpha
  if (is.null(alpha)) alpha <- 1
  scales::alpha(value, alpha)
}

ggplot_styled_layer_mapping <- function(layer) {
  mapping <- layer$computed_mapping
  # The final colour can depend on another aesthetic, even when its input
  # is fixed. Such staged mappings cannot be edited independently.
  if (any(vapply(mapping, function(value) {
    any(all.names(rlang::get_expr(value)) %in% c("after_scale", "stage"))
  }, logical(1L)))) return(NULL)
  setdiff(as.character(names(mapping)), names(layer$aes_params))
}

ggplot_layer_has_legend_key <- function(built, layer, mapped) {
  if (identical(layer$show.legend, FALSE)) return(FALSE)
  guides <- built$plot$guides
  # A continuous colour bar describes its scale. Other guides may draw
  # a key for this layer, which must agree with the painted series.
  any(vapply(seq_along(guides$params), function(i) {
    !inherits(guides$guides[[i]], "GuideColourbar") &&
      (length(intersect(guides$params[[i]]$aesthetic, mapped)) > 0L ||
       !identical(layer$show.legend, NA))
  }, logical(1L)))
}

# Bind a drawn native layer to its actual panel cell. Facet grids and wraps
# can pack the same PANEL values in different row and column orders.
ggplot_cartesian_panels <- function(table, layout) {
  if (!identical(class(layout$coord)[[1L]], "CoordCartesian")) return(NULL)
  slots <- which(grepl("^panel($|-)", table$layout$name))
  if (!length(slots)) return(NULL)
  cells <- layout$layout
  single <- length(slots) == 1L && identical(table$layout$name[[slots]], "panel")
  facets <- setdiff(names(cells), c("PANEL", "ROW", "COL", "SCALE_X", "SCALE_Y", "COORD"))
  if (!single && (!class(layout$facet)[[1L]] %in% c("FacetWrap", "FacetGrid") || !length(facets))) {
    return(NULL)
  }
  members <- lapply(table$grobs[slots], function(grob) grob$childrenOrder)
  drawn_names <- unlist(members, use.names = FALSE)
  placements <- data.frame(grob = drawn_names, slot = rep(slots, lengths(members)),
                           child = sequence(lengths(members)))
  placements <- placements[!(duplicated(drawn_names) | duplicated(drawn_names, fromLast = TRUE)), , drop = FALSE]
  list(placements = placements, cells = cells, facets = facets, single = single)
}

ggplot_panel_draws <- function(panels, members, data) {
  groups <- if (is.null(names(members))) list(data) else split(data, data$PANEL)
  if (length(groups) != length(members) || !identical(names(groups), names(members))) return(list())
  draws <- vector("list", length(members))
  for (i in seq_along(members)) {
    member <- members[[i]]
    position <- match(member$name %||% "", panels$placements$grob)
    if (is.na(position)) next
    rows <- groups[[i]]
    id <- unique(as.character(rows$PANEL))
    if (length(id) != 1L) next
    panel <- match(id, as.character(panels$cells$PANEL))
    if (is.na(panel)) next
    facet <- if (!panels$single) lapply(panels$cells[panel, panels$facets, drop = FALSE], as.character)
    draws[[i]] <- list(grob = member, rows = rows, facet = facet, panel_id = panels$cells$PANEL[[panel]],
                       slot = panels$placements$slot[[position]], child = panels$placements$child[[position]])
  }
  Filter(Negate(is.null), draws)
}

ggplot_layer_label <- function(name, target) {
  label <- paste(name, target$index)
  if (length(target$facet)) {
    label <- paste(label, paste(unlist(target$facet), collapse = " / "), sep = " \u00b7 ")
  }
  label
}

ggplot_drawn_panel_viewport <- function(drawn, table, slot, child) {
  path <- c(table$name, ggplot_cell_name(table$layout[slot, ]), table$grobs[[slot]]$name)
  row <- which(drawn$name == child & drawn$gPath == paste(path, collapse = "::"))
  if (length(row) != 1L) return(NULL)
  path <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
  if (length(path) >= 2L && path[[1L]] == "ROOT") {
    do.call(grid::vpPath, as.list(path[-1L]))
  }
}

ggplot_line_guide_keys <- function(built, layer, key_name, mapped) {
  if (identical(layer$show.legend, FALSE)) return(integer())
  if (!identical(layer$show.legend, NA)) return(NULL)
  guides <- built$plot$guides
  indices <- which(vapply(guides$params, function(params) {
    length(intersect(params$aesthetic, mapped)) > 0L
  }, logical(1L)))
  indices <- indices[!vapply(guides$guides[indices], inherits, logical(1L), "GuideColourbar")]
  if (!length(indices)) return(integer())
  # A trained legend names its contributions by geom, not by layer id.
  # Only edit a key when that geom uniquely identifies the owning layer.
  kind <- class(layer$geom)[[1L]]
  same_kind <- vapply(built$plot$layers, function(other) {
    identical(class(other$geom)[[1L]], kind)
  }, logical(1L))
  if (sum(same_kind) != 1L || !identical(as.list(layer$geom)$draw_key, ggplot2::draw_key_path)) {
    return(NULL)
  }
  for (index in indices) {
    params <- guides$params[[index]]
    key <- params$decor[[key_name]]
    if (!identical(class(guides$guides[[index]])[[1L]], "GuideLegend") ||
        !is.function(key$draw_key) ||
        !is.data.frame(key$data) || !all(c("linewidth", "linetype") %in% names(key$data)) ||
        length(intersect(names(params$override.aes), c("linewidth", "linetype")))) return(NULL)
  }
  indices
}

ggplot_errorbar_caps <- function(line, flipped) {
  runs <- rle(line$id)
  if (length(unique(flipped)) != 1L || !is.logical(flipped) || anyNA(flipped) ||
      any(runs$lengths != 8L) || anyDuplicated(runs$values)) return(NULL)
  points <- matrix(seq_along(line$id), nrow = 8L)
  horizontal <- isTRUE(flipped[[1L]])
  along <- as.numeric(if (horizontal) line$y else line$x)
  across <- as.numeric(if (horizontal) line$x else line$y)
  ends <- points[c(1L, 2L, 4L, 5L, 7L, 8L), , drop = FALSE]
  separators <- points[c(3L, 6L), , drop = FALSE]
  if (!all(is.finite(along[ends]) & is.finite(across[ends])) ||
      !all(is.na(along[separators]) & is.na(across[separators])) ||
      !all(along[points[4L, ]] == along[points[5L, ]]) ||
      !all(across[points[1L, ]] == across[points[4L, ]] &
           across[points[2L, ]] == across[points[4L, ]] &
           across[points[7L, ]] == across[points[5L, ]] &
           across[points[8L, ]] == across[points[5L, ]])) return(NULL)
  list(points = points, axis = if (horizontal) "y" else "x")
}

ggplot_cap_span <- function(line, caps) {
  if (!is.null(line$tavotto_cap_span)) return(line$tavotto_cap_span)
  positions <- line[[caps$axis]]
  positions[caps$points[2L, 1L]] - positions[caps$points[1L, 1L]]
}

ggplot_edit_errorbar_caps <- function(line, caps, length_pt, width_pt) {
  points <- caps$points
  span <- ggplot_cap_span(line, caps)
  if (!is.null(length_pt)) {
    span <- grid::unit(length_pt / 72, "inches")
    positions <- line[[caps$axis]]
    centre <- positions[points[c(4L, 4L, 5L, 5L), , drop = FALSE]]
    positions[points[c(1L, 2L, 7L, 8L), , drop = FALSE]] <-
      centre + grid::unit(rep(c(-1, 1), length.out = length(centre)) * length_pt / 144, "inches")
    line[[caps$axis]] <- positions
  }
  line$tavotto_cap_span <- span
  hide_caps <- isTRUE(length_pt == 0)
  if (!is.null(width_pt) || hide_caps) {
    segments <- if (hide_caps) points[c(4L, 5L), , drop = FALSE] else
      points[c(1L, 2L, 4L, 5L, 7L, 8L), , drop = FALSE]
    segment_count <- nrow(segments) / 2L
    line$x <- line$x[segments]
    line$y <- line$y[segments]
    line$id <- rep(seq_len(length(segments) / 2L), each = 2L)
    source_strokes <- rep(seq_len(ncol(points)), each = segment_count)
    for (key in names(line$gp)) {
      value <- line$gp[[key]]
      if (length(value) != 1L) {
        line$gp[[key]] <- rep_len(value, ncol(points))[source_strokes]
      }
    }
    if (!hide_caps) {
      stroke <- rep_len(line$gp$lwd, length(line$id) / 2L)
      stroke[rep(c(TRUE, FALSE, TRUE), ncol(points))] <- width_pt / ggplot_lwd_pt
      line$gp$lwd <- stroke
    }
  }
  line
}

ggplot_line_targets <- function(built, panels, layer_grobs) {
  if (is.null(panels)) return(list())
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    interval <- kind %in% c("GeomErrorbar", "GeomErrorbarh")
    if (!interval && !kind %in% c("GeomLine", "GeomPath")) next
    mapping <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapping)) next
    key_name <- if (kind == "GeomLine") "geom_line" else "geom_path"
    guide_keys <- integer()
    if (interval) {
      if (ggplot_layer_has_legend_key(built, layer, mapping)) next
    } else {
      guide_keys <- ggplot_line_guide_keys(built, layer, key_name, mapping)
      if (is.null(guide_keys)) next
    }
    if (length(intersect(mapping, c("size", "linewidth", "linetype")))) next
    # One shared legend guide cannot represent different stroke styles for
    # the same series in two facets.
    if (!panels$single && length(guide_keys)) next
    for (draw in ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])) {
      line <- draw$grob
      suffix <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      if (!inherits(line, "polyline") || !is.null(line$arrow) || !is.null(line$vp) ||
          length(line$x) < 2L || length(line$x) > 4096L ||
          length(line$id.lengths) > 1L) next
      if (!is.null(line$id) && (length(line$id) != length(line$x) || anyNA(line$id))) next
      if (interval && is.null(line$id)) next
      width <- unique(line$gp$lwd)
      if (!is_positive_ggplot_number(width)) next
      rgba <- tryCatch(grDevices::col2rgb(unique(line$gp$col), alpha = TRUE), error = function(error) NULL)
      if (is.null(rgba) || !ncol(rgba) || any(rgba[4L, ] == 0L)) next
      lty <- unique(line$gp$lty)
      if (is.numeric(lty) && length(lty) == 1L && lty %in% 1:4) {
        lty <- unname(ggplot_line_types[[lty]])
      }
      style <- names(ggplot_line_types)[match(lty, ggplot_line_types, nomatch = 0L)]
      if (length(style) != 1L) next
      gid <- paste0("layer.", index, suffix, if (interval) ".errorbar" else ".line")
      facts <- list(kind, draw$rows, line$gp)
      if (!panels$single) facts <- c(facts, list(draw$facet))
      if (length(guide_keys)) {
        facts <- c(facts, lapply(built$plot$guides$params[guide_keys], function(params) {
          list(key = params$key, stroke = params$decor[[key_name]]$data)
        }))
      }
      context <- rlang::hash(facts)
      targets[[gid]] <- list(
        slot = draw$slot, child = line$name, index = index, context = context, interval = interval,
        facet = draw$facet,
        caps = if (interval) ggplot_errorbar_caps(line, draw$rows$flipped_aes),
        guide_keys = guide_keys, key_name = key_name,
        clip = identical(built$layout$coord$clip, "on"),
        width = width * ggplot_lwd_pt, style = style,
        color = if (!"colour" %in% mapping && !length(guide_keys) && ncol(rgba) == 1L) {
          grDevices::rgb(rgba[1L], rgba[2L], rgba[3L], maxColorValue = 255)
        },
        alpha = if (!interval || (!"alpha" %in% mapping && length(unique(rgba[4L, ])) == 1L)) rgba[4L, 1L]
      )
    }
  }
  contexts <- vapply(targets, function(target) target$context, character(1L))
  targets[!(duplicated(contexts) | duplicated(contexts, fromLast = TRUE))]
}

ggplot_path_runs <- function(x, y, id, min_points) {
  if (is.null(id)) id <- rep.int(1L, length(x))
  valid <- is.finite(x) & is.finite(y)
  new_path <- c(TRUE, id[-1L] != id[-length(id)] | !valid[-length(valid)])
  groups <- split(which(valid), cumsum(new_path)[valid])
  unname(groups[lengths(groups) >= min_points])
}

ggplot_device_paths <- function(x, y, id, closed, frame_pt) {
  positions <- grid::deviceLoc(x, y, valueOnly = TRUE)
  x <- positions$x / (frame_pt[[1L]] / 72)
  y <- 1 - positions$y / (frame_pt[[2L]] / 72)
  lapply(ggplot_path_runs(x, y, id, if (closed) 3L else 2L), function(i) {
    list(points = Map(function(x, y) list(x, y), x[i], y[i]), closed = closed)
  })
}

ggplot_panel_clip <- function(frame_pt) {
  low <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
  high <- grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE)
  as.list(ggplot_bbox(low, high, frame_pt))
}

measure_ggplot_lines <- function(table, targets, frame_pt) {
  if (!length(targets)) return(targets)
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    path <- c(table$name, ggplot_cell_name(table$layout[target$slot, ]),
              table$grobs[[target$slot]]$name)
    row <- which(drawn$name == target$child & drawn$gPath == paste(path, collapse = "::"))
    if (length(row) != 1L) next
    viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
    if (length(viewport) < 2L || viewport[[1L]] != "ROOT") next
    geometry <- tryCatch({
      grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
      line <- grid::grid.get(do.call(grid::gPath, as.list(c(path, target$child))), strict = TRUE)
      width <- max(line$gp$lwd) * ggplot_lwd_pt
      paths <- ggplot_device_paths(line$x, line$y, line$id, FALSE, frame_pt)
      if (!length(paths) || !is_positive_ggplot_number(width)) stop("Invalid native R stroke geometry.")
      visible <- any(grDevices::col2rgb(line$gp$col, alpha = TRUE)[4L, ] > 0)
      geometry <- list(kind = if (target$interval || length(paths) != 1L) "multi_path" else "polyline",
                       paths = paths, fill = FALSE, stroke = visible, stroke_pt = width,
                       paint_order = match(line$name, table$grobs[[target$slot]]$childrenOrder))
      if (target$clip) geometry$clip <- ggplot_panel_clip(frame_pt)
      pad <- width / (2 * frame_pt)
      bbox <- ggplot_paths_bbox(paths, pad)
      capsize <- if (!is.null(target$caps)) {
        span <- ggplot_cap_span(line, target$caps)
        abs(grid::convertUnit(span, "inches", axisFrom = target$caps$axis,
                              typeFrom = "dimension", valueOnly = TRUE)) * 72
      }
      list(bbox = as.list(bbox), geometry = geometry, capsize = capsize)
    }, error = function(error) NULL, finally = grid::upViewport(0))
    result[[gid]] <- geometry
  }
  result
}

ggplot_paths_bbox <- function(paths, pad = c(0, 0)) {
  bounds <- vapply(paths, function(path) {
    points <- do.call(rbind, path$points)
    c(range(points[, 1L]), range(points[, 2L]))
  }, numeric(4L))
  x <- range(bounds[1:2, ])
  y <- range(bounds[3:4, ])
  c(x[[1L]] - pad[[1L]], y[[1L]] - pad[[2L]], diff(x) + 2 * pad[[1L]], diff(y) + 2 * pad[[2L]])
}

ggplot_line_style <- function(target, patch) {
  change <- list()
  max_width <- if (target$interval) 5 else 20
  for (prop in names(patch)) {
    value <- patch[[prop]]
    if (prop == "linewidth" && is_positive_ggplot_number(value) && value >= 0.1 && value <= max_width) {
      change$lwd <- value / ggplot_lwd_pt
    } else if (!target$interval && prop == "linestyle" && is.character(value) && length(value) == 1L &&
               value %in% names(ggplot_line_types)) {
      change$lty <- unname(ggplot_line_types[[value]])
    } else if (prop == "color" && !is.null(target$color) && is.character(value) &&
               length(value) == 1L && !is.na(value) && grepl("^#[0-9A-Fa-f]{6}$", value)) {
      change$col <- scales::alpha(value, target$alpha / 255)
    } else if (target$interval && !is.null(target$alpha) && prop == "alpha" && is.numeric(value) &&
               length(value) == 1L && is.finite(value) && value >= 0 && value <= 1) {
      next
    } else if (!is.null(target$caps) && is.numeric(value) && length(value) == 1L &&
               is.finite(value) &&
               (prop == "capsize" && value >= 0 && value <= 15 ||
                prop == "cap_thickness" && value >= 0.1 && value <= 5)) {
      next
    } else {
      stop("Invalid native R curve style.")
    }
  }
  change
}

ggplot_edit_line <- function(table, target, patch) {
  panel <- table$grobs[[target$slot]]
  line <- panel$children[[target$child]]
  change <- ggplot_line_style(target, patch)
  line$gp[names(change)] <- change
  if (!is.null(patch$alpha)) line$gp$col <- scales::alpha(line$gp$col, patch$alpha)
  if (!is.null(patch$capsize) || !is.null(patch$cap_thickness)) {
    caps <- ggplot_errorbar_caps(line, target$caps$axis == "y")
    if (is.null(caps)) stop("The native R error bar caps changed.")
    line <- ggplot_edit_errorbar_caps(line, caps, patch$capsize, patch$cap_thickness)
  }
  panel$children[[target$child]] <- line
  table$grobs[[target$slot]] <- panel
  table
}

capture_ggplot <- function(plot, frame_pt, palettes = NULL) {
  if (!inherits(plot, "ggplot")) stop("The selected object is not a ggplot.")
  measured <- with_svg_frame(frame_pt, function(capture) {
    colour_capture <- new.env(parent = emptyenv())
    if (!is.null(palettes)) plot <- ggplot_observe_colour_maps(plot, colour_capture)
    built <- ggplot2::ggplot_build(plot)
    layout_input <- built
    layout_input$plot$guides <- ggplot_colourbar_guides(built$plot$guides, built$plot$scales)
    captured_layers <- new.env(parent = emptyenv())
    layers <- built$plot$layers
    captured_layers$grobs <- vector("list", length(layers))
    layout_input$plot$layers <- Map(function(layer, i) {
      ggplot_watch_layer(ggplot_mirror_raster_layer(layer, built$layout$coord), i, captured_layers)
    }, layers, seq_along(layers))
    table <- ggplot2::ggplot_gtable(layout_input)
    grid::grid.newpage()
    grid::grid.draw(table)
    colourbar_titles <- ggplot_colourbar_titles(built)
    text_targets <- ggplot_text_targets(table, frame_pt, colourbar_titles)
    tick_targets <- if (all(lengths(captured_layers$grobs) > 0L)) {
      ggplot_tick_targets(built, text_targets, colourbar_titles)
    }
    colour_scales <- list()
    if (all(lengths(captured_layers$grobs) > 0L)) {
      colour_scales <- ggplot_colour_scale_targets(
        built, table, text_targets, colourbar_titles, colour_capture$maps
      )
      bounds <- measure_ggplot_colour_scales(table, colour_scales, frame_pt)
      colour_scales <- colour_scales[names(bounds)]
      for (gid in names(colour_scales)) colour_scales[[gid]]$bbox <- bounds[[gid]]
    }
    panels <- ggplot_cartesian_panels(table, built$layout)
    line_targets <- ggplot_line_targets(built, panels, captured_layers$grobs)
    line_geometry <- measure_ggplot_lines(table, line_targets, frame_pt)
    line_targets <- line_targets[names(line_geometry)]
    axes_target <- ggplot_grid_target(built, table, ggplot_axes_target(built, table))
    axes_target <- ggplot_frame_target(built, table, axes_target)
    axes_bbox <- measure_ggplot_axes(table, axes_target, frame_pt)
    has_sized_title <- any(vapply(text_targets, function(text) !is.null(text$title_dimension), logical(1L)))
    rebuild_guides <- has_sized_title || !is.null(axes_target) || length(tick_targets) || length(colour_scales) ||
      any(vapply(line_targets, function(line) length(line$guide_keys) > 0L, logical(1L)))
    point_targets <- ggplot_point_targets(built, panels, captured_layers$grobs)
    point_boxes <- measure_ggplot_points(table, point_targets, frame_pt)
    point_targets <- point_targets[names(point_boxes)]
    for (gid in names(point_targets)) point_targets[[gid]]$bbox <- point_boxes[[gid]]
    bar_targets <- ggplot_bar_targets(built, panels, captured_layers$grobs)
    bar_geometry <- measure_ggplot_bars(table, bar_targets, frame_pt)
    bar_targets <- bar_targets[names(bar_geometry)]
    ribbon_targets <- ggplot_ribbon_targets(built, panels, captured_layers$grobs)
    ribbon_geometry <- measure_ggplot_ribbons(table, ribbon_targets, frame_pt)
    ribbon_targets <- ribbon_targets[names(ribbon_geometry)]
    legend_targets <- ggplot_legend_boxes(table)
    legend_boxes <- measure_ggplot_legends(legend_targets, frame_pt)
    legend_targets <- legend_targets[!vapply(legend_boxes, is.null, logical(1L))]
    for (gid in names(legend_targets)) legend_targets[[gid]]$bbox <- legend_boxes[[gid]]
    list(
      table = table,
      text_targets = text_targets,
      tick_targets = tick_targets,
      colour_scales = colour_scales,
      palettes = palettes,
      built = if (rebuild_guides) built,
      layer_grobs = if (rebuild_guides) captured_layers$grobs,
      axes_target = axes_target,
      axes_bbox = axes_bbox,
      line_targets = line_targets,
      line_geometry = line_geometry,
      point_targets = point_targets,
      bar_targets = bar_targets,
      bar_geometry = bar_geometry,
      ribbon_targets = ribbon_targets,
      ribbon_geometry = ribbon_geometry,
      legend_targets = legend_targets,
      layers = built$data,
      panel_ranges = lapply(built$layout$panel_params, function(panel) {
        list(x = panel$x.range, y = panel$y.range)
      })
    )
  })
  # Measure on the same device type and frame as the final draw. Use a fresh
  # device for the artifact so viewport inspection does not add SVG nodes.
  state <- c(list(frame_pt = frame_pt), measured$value)
  svg <- tryCatch(render_ggplot(state), tavotto_legend_svg = function(error) NULL)
  if (is.null(svg)) {
    state$legend_targets <- list()
    svg <- render_ggplot(state)
  }
  list(state = state, svg = svg)
}

edit_ggplot_text <- function(table, slots, path, paint) {
  if (!length(slots)) return(grid::editGrob(table, path, gp = paint, strict = TRUE))
  slot <- slots[[1L]]
  if (is.character(slot)) {
    table$children[[slot]] <- edit_ggplot_text(table$children[[slot]], slots[-1L], path, paint)
  } else {
    table$grobs[[slot]] <- edit_ggplot_text(table$grobs[[slot]], slots[-1L], path, paint)
  }
  table
}

ggplot_face_options <- list(weight = c("normal", "bold"), style = c("normal", "italic"))
ggplot_font_families <- c("sans-serif" = "sans", serif = "serif", monospace = "mono")

ggplot_installed_title_fonts <- function() {
  fonts <- tryCatch(
    systemfonts::font_info(family = unname(ggplot_font_families)),
    error = function(error) NULL
  )
  if (is.null(fonts)) return(character())
  # Cairo and svglite interpret blank and generic names differently. Give
  # both devices the concrete family resolved by the native font system.
  valid <- !is.na(fonts$family) & nzchar(fonts$family) & !is.na(fonts$monospace) &
    (fonts$monospace == (ggplot_font_families == "mono"))
  stats::setNames(fonts$family[valid], names(ggplot_font_families)[valid])
}

ggplot_title_family <- function(target) {
  if (!length(target$title_fonts)) return(NULL)
  family <- target$paint$fontfamily
  if (is.null(family) || identical(family, "")) return("sans-serif")
  if (!is.character(family) || length(family) != 1L || is.na(family)) return(NULL)
  index <- match(family, ggplot_font_families)
  if (is.na(index)) family else names(ggplot_font_families)[[index]]
}

is_positive_ggplot_number <- function(value) {
  is.numeric(value) && length(value) == 1L && is.finite(value) && value > 0
}

ggplot_text_cex <- function(paint) {
  if (is.null(paint$cex)) 1 else paint$cex
}

ggplot_text_edit <- function(edit, tick) {
  guide_props <- if (is.null(tick$size)) ggplot_tick_props else c(ggplot_tick_props, "fontsize")
  edit[setdiff(names(edit), guide_props)]
}

ggplot_text_paint <- function(target, edits) {
  paint <- target$paint
  face <- target$face
  family <- ggplot_title_family(target)
  for (prop in names(edits)) {
    value <- edits[[prop]]
    if (prop == "fontsize") {
      if (is.null(target$title_dimension)) stop("The requested native R text style is unavailable.")
      if (!is_positive_ggplot_number(value)) stop("The native R font size must be a positive number.")
      paint$fontsize <- value / ggplot_text_cex(paint)
      next
    }
    if (!is.character(value) || length(value) != 1L || is.na(value)) {
      stop("Invalid native R text style.")
    }
    if (prop == "color") {
      if (!grepl("^#[0-9A-Fa-f]{6}$", value)) {
        stop("The native R text color must be an RGB hex color.")
      }
      paint$col <- value
    } else if (prop == "fontfamily" && !is.null(family) &&
               value %in% c(family, names(target$title_fonts))) {
      if (value != family) paint$fontfamily <- target$title_fonts[[value]]
    } else if (!is.null(face[[prop]]) && value %in% ggplot_face_options[[prop]]) {
      face[[prop]] <- value
    } else {
      stop("The requested native R text style is unavailable.")
    }
  }
  if (!identical(face, target$face)) {
    # grid encodes both attributes in one font slot. Undo and partial changes
    # always start from the captured grob, including its other face attribute.
    paint$font <- 1L + as.integer(face$weight == "bold") + 2L * as.integer(face$style == "italic")
  }
  list(paint = paint, face = face)
}

ggplot_title_theme <- function(targets, edits) {
  # Rebuild title layout through the same ggplot2 theme operation as an R script.
  # The guide table retains the built data and drawing work from the capture.
  result <- list()
  for (gid in intersect(names(edits), names(ggplot_title_theme_names))) {
    edit <- edits[[gid]]
    if (is.null(edit$fontsize) && is.null(edit$fontfamily)) next
    target <- targets[[gid]]
    styled <- ggplot_text_paint(target, edit)
    face <- NULL
    if (!is.null(edit$weight) || !is.null(edit$style)) {
      face <- c("plain", "bold", "italic", "bold.italic")[[styled$paint$font]]
    }
    result[[gid]] <- ggplot2::element_text(
      size = edit$fontsize, family = if (!is.null(edit$fontfamily)) styled$paint$fontfamily,
      colour = edit$color, face = face
    )
  }
  result
}

ggplot_legend_position <- function(edit) {
  if (!identical(names(edit), "loc_frac")) stop("The requested native R legend edit is unavailable.")
  value <- edit$loc_frac
  if (is.list(value) && is.null(names(value)) &&
      all(vapply(value, function(x) is.numeric(x) && length(x) == 1L, logical(1L)))) {
    value <- unlist(value, use.names = FALSE)
  }
  if (!is.numeric(value) || length(value) != 2L || any(!is.finite(value))) {
    stop("The native R legend position must contain two finite numbers.")
  }
  value
}

makeContext.tavottoLegendPosition <- function(x) {
  # Grid calls makeContext while still in the original gtable cell. Resolve
  # the absolute figure anchor against that cell on the actual output device.
  p <- x$tavotto_position
  frame <- grDevices::dev.size("in")
  origin <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
  xy <- c(p[[1L]], 1 - p[[2L]]) * frame - c(origin$x, origin$y)
  if (any(!is.finite(xy))) stop("The native R legend position is outside the device range.")
  just <- x$vp$valid.just
  x$vp$x <- grid::unit(xy[[1L]], "in") + just[[1L]] * x$vp$width
  x$vp$y <- grid::unit(xy[[2L]], "in") + just[[2L]] * x$vp$height
  NextMethod()
}

registerS3method("makeContext", "tavottoLegendPosition", makeContext.tavottoLegendPosition,
                 envir = asNamespace("grid"))

ggplot_guide_stroke_edits <- function(state, edits) {
  gids <- intersect(names(edits), names(state$line_targets))
  edits[gids[vapply(state$line_targets[gids], function(line) length(line$guide_keys) > 0L,
                    logical(1L))]]
}

ggplot_table_for <- function(state, edits) {
  table <- state$table
  layout <- state$built$layout
  text_targets <- state$text_targets
  legend_targets <- state$legend_targets
  stops <- NULL
  axes <- NULL
  tick_values <- NULL
  axes_edits <- edits[[ggplot_axes_gid]]
  grid_edits <- axes_edits[intersect(names(axes_edits), ggplot_grid_keys)]
  frame_edits <- axes_edits[intersect(names(axes_edits), ggplot_frame_keys)]
  if (length(frame_edits)) ggplot_validate_frame_edits(state$axes_target$frame, frame_edits)
  axis_changes <- ggplot_axis_edits(
    state$axes_target, axes_edits[setdiff(names(axes_edits), c(names(grid_edits), names(frame_edits)))])
  layer_targets <- state[c("line_targets", "point_targets", "bar_targets", "ribbon_targets")]
  tick_edits <- Filter(length, lapply(edits, function(edit) edit[intersect(names(edit), ggplot_tick_props)]))
  axis_sizes <- Filter(Negate(is.null), lapply(names(edits), function(gid) {
    if (!is.null(state$tick_targets[[gid]]$size)) edits[[gid]]$fontsize
  }) |> stats::setNames(names(edits)))
  colour_edits <- edits[intersect(names(edits), names(state$colour_scales))]
  guide_strokes <- ggplot_guide_stroke_edits(state, edits)
  title_edits <- ggplot_title_theme(text_targets, edits)
  title_theme <- stats::setNames(title_edits, unname(ggplot_title_theme_names[names(title_edits)]))
  if (length(tick_edits) || length(colour_edits) || length(axis_sizes) || length(guide_strokes) ||
      length(axis_changes) || length(grid_edits) || length(title_edits)) {
    make_guides <- function() ggplot_guide_table(
      state, tick_edits, colour_edits, axis_sizes, guide_strokes, axis_changes, grid_edits, title_theme
    )
    # Recreate the title cells on the same metrics device used by the initial
    # capture, then draw the resulting table on the requested output device.
    guides <- if (length(title_edits)) {
      with_svg_frame(state$frame_pt, function(capture) make_guides())$value
    } else make_guides()
    table <- guides$table
    layout <- guides$layout
    stops <- guides$stops
    axes <- guides$axes
    tick_values <- guides$tick_values
    if (!identical(table$layout$name, state$table$layout$name)) {
      stop("The native R guide layout changed while formatting.")
    }
    # Redrawing the guide layout gives new grid paths even for retained
    # content. Match each original cell before moving or styling it.
    for (gid in names(text_targets)) {
      target <- text_targets[[gid]]
      if (gid %in% guides$hidden_ticks) {
        text_targets[[gid]]$hidden <- TRUE
        next
      }
      group <- if (!is.null(target$tick_labels)) target$label
      current <- ggplot_text_target(table, target$slots, target$role, target$title_child, group)
      changes_ticks <- !is.null(tick_edits[[gid]]) || gid %in% guides$guide_ticks ||
        (target$role == "ticks" && substr(gid, 6L, 6L) %in% names(axis_changes))
      if (is.null(current) || !identical(target$label, current$label) ||
          (!changes_ticks && !identical(target$tick_labels, current$tick_labels))) {
        stop("The native R text layout changed while formatting.")
      }
      size <- axis_sizes[[gid]]
      if (!is.null(size) && !isTRUE(all.equal(size, current$paint$fontsize * ggplot_text_cex(current$paint)))) {
        stop("The native R axis guide did not apply the requested font size.")
      }
      target[names(current)] <- current
      text_targets[[gid]] <- target
    }
    for (gid in names(legend_targets)) {
      slot <- legend_targets[[gid]]$slot
      viewport <- table$grobs[[slot]]$vp
      if (!inherits(viewport, "viewport")) stop("The native R legend layout changed while formatting.")
      legend_targets[[gid]]$viewport <- c(
        table$name, ggplot_cell_name(table$layout[slot, ]), viewport$name
      )
    }
    if (length(axis_changes)) {
      layer_targets <- lapply(layer_targets, function(targets) {
        ggplot_rebind_layer_targets(table, targets, guides$layer_grobs)
      })
    }
  }
  for (gid in names(edits)) {
    if (gid == ggplot_axes_gid || !is.null(colour_edits[[gid]]) || gid %in% names(title_edits)) next
    line <- layer_targets$line_targets[[gid]]
    if (!is.null(line)) {
      table <- ggplot_edit_line(table, line, edits[[gid]])
      next
    }
    points <- layer_targets$point_targets[[gid]]
    if (!is.null(points)) {
      table <- ggplot_edit_points(table, points, edits[[gid]])
      next
    }
    bars <- layer_targets$bar_targets[[gid]]
    if (!is.null(bars)) {
      table <- ggplot_edit_bars(table, bars, edits[[gid]], layout)
      next
    }
    ribbon <- layer_targets$ribbon_targets[[gid]]
    if (!is.null(ribbon)) {
      table <- ggplot_edit_ribbon(table, ribbon, edits[[gid]])
      next
    }
    legend <- legend_targets[[gid]]
    if (!is.null(legend)) {
      box <- table$grobs[[legend$slot]]
      box$tavotto_position <- ggplot_legend_position(edits[[gid]])
      class(box) <- c("tavottoLegendPosition", class(box))
      table$grobs[[legend$slot]] <- box
      next
    }
    target <- text_targets[[gid]]
    if (is.null(target)) stop("The requested native R text is unavailable.")
    patch <- ggplot_text_edit(edits[[gid]], state$tick_targets[[gid]])
    if (!length(patch)) next
    paint <- ggplot_text_paint(target, patch)$paint
    if (isTRUE(target$hidden)) next
    table <- edit_ggplot_text(table, target$slots, target$edit_path, paint)
  }
  if (length(frame_edits)) {
    table <- ggplot_frame_edit(table, state$axes_target$slot, state$axes_target$frame$fallback, frame_edits)
  }
  c(list(table = table, text = text_targets, legends = legend_targets, stops = stops, axes = axes,
         tick_values = tick_values),
    layer_targets)
}

ggplot_record_legend <- function(capture, marked, gid) {
  svg <- as.character(capture())
  suffix <- substring(svg, max(1L, nchar(svg) - 768L))
  # The over-composite just emitted the device's own <use>, followed only
  # by the closing groups of the current drawing context.
  pattern <- "(<use href='#group-[0-9]+'[^<]*/>)[[:space:]]*(</g>[[:space:]]*)+</svg>[[:space:]]*$"
  match <- regmatches(suffix, regexec(pattern, suffix, perl = TRUE))[[1L]]
  if (length(match)) marked[[gid]] <- match[[2L]]
}

render_ggplot <- function(state, edits = list()) {
  marked <- new.env(parent = emptyenv())
  svg <- with_svg_frame(state$frame_pt, function(capture) {
    grid::grid.newpage()
    table <- ggplot_table_for(state, edits)$table
    for (gid in names(state$legend_targets)) {
      slot <- state$legend_targets[[gid]]$slot
      record <- grid::recordGrob(record_legend(capture, marked, gid), list(
        record_legend = ggplot_record_legend, capture = capture, marked = marked, gid = gid
      ))
      table$grobs[[slot]] <- grid::grobTree(grid::groupGrob(table$grobs[[slot]]), record)
    }
    grid::grid.draw(table)
  })$svg
  for (gid in names(state$legend_targets)) {
    use <- marked[[gid]]
    if (is.null(use) || lengths(regmatches(svg, gregexpr(use, svg, fixed = TRUE))) != 1L) {
      stop(errorCondition("The native SVG legend group could not be identified.",
                          class = "tavotto_legend_svg"))
    }
    tagged <- sub("<use ", paste0("<use id='", gid, "' "), use, fixed = TRUE)
    svg <- sub(use, tagged, svg, fixed = TRUE)
  }
  svg
}

open_ggplot_pdf <- function(frame_pt, path) {
  if (!capabilities("cairo")) stop("The selected R cannot produce Cairo PDF.")
  before <- grDevices::dev.cur()
  diagnostics <- character()
  withCallingHandlers(
    grDevices::cairo_pdf(filename = path, width = frame_pt[[1L]] / 72,
                         height = frame_pt[[2L]] / 72, onefile = TRUE),
    warning = function(warning) diagnostics <<- c(diagnostics, conditionMessage(warning))
  )
  after <- grDevices::dev.cur()
  if (identical(before, after)) {
    stop(paste(c("The selected R could not open its Cairo PDF device.", diagnostics),
               collapse = " "))
  }
  after
}

export_ggplot_pdf <- function(state, edits, path) {
  device <- open_ggplot_pdf(state$frame_pt, path)
  on.exit(grDevices::dev.off(which = device))
  grid::grid.newpage()
  table <- ggplot_table_for(state, edits)$table
  grid::grid.draw(table)
  invisible(NULL)
}

preview_ggplot_png <- function(state, edits, path, width, with_manifest) {
  if (!is.numeric(width) || length(width) != 1L || !is.finite(width) || width < 1) {
    stop("The native R preview needs a positive pixel width.")
  }
  height <- max(1, round(width * state$frame_pt[[2L]] / state$frame_pt[[1L]]))
  if (!is.finite(height) || width * height > 20000000) {
    stop("The native R preview exceeds the pixel budget.")
  }
  before <- grDevices::dev.cur()
  grDevices::png(path, width = width, height = height, units = "px", type = "cairo", bg = "transparent",
                 res = width * 72 / state$frame_pt[[1L]])
  device <- grDevices::dev.cur()
  if (identical(device, before)) stop("The selected R could not open its PNG device.")
  on.exit(grDevices::dev.off(which = device))
  grid::grid.newpage()
  scene <- ggplot_table_for(state, edits)
  grid::grid.draw(scene$table)
  if (with_manifest) {
    # Measure the actual raster canvas, including any pixel rounding.
    frame_pt <- grDevices::dev.size("in") * 72
    return(list(text = measure_ggplot_targets(scene$text, frame_pt),
                legends = measure_ggplot_legends(scene$legends, frame_pt),
                axes = scene$axes,
                axes_bbox = measure_ggplot_axes(scene$table, state$axes_target, frame_pt),
                colour_scales = if (length(state$colour_scales)) {
                  measure_ggplot_colour_scales(scene$table, state$colour_scales, frame_pt)
                },
                tick_values = scene$tick_values,
                scale_stops = scene$stops,
                points = measure_ggplot_points(scene$table, scene$point_targets, frame_pt, !is.null(scene$axes)),
                bars = measure_ggplot_bars(scene$table, scene$bar_targets, frame_pt, !is.null(scene$axes)),
                ribbons = measure_ggplot_ribbons(scene$table, scene$ribbon_targets, frame_pt),
                lines = measure_ggplot_lines(scene$table, scene$line_targets, frame_pt)))
  }
  invisible(NULL)
}

ggplot_manifest <- function(state, stem, edits = list(), measured = NULL) {
  # A resized title can move the entire plot. Re-measure even targets whose
  # own paint was left alone so the hit regions follow the rendered layout.
  legend_gids <- names(state$legend_targets)
  reflow <- any(vapply(edits, function(edit) any(c("fontsize", "fontfamily", ggplot_tick_props) %in% names(edit)),
                      logical(1L))) ||
    any(names(edits) %in% c(ggplot_axes_gid, legend_gids, names(state$colour_scales))) ||
    length(ggplot_guide_stroke_edits(state, edits)) > 0L
  line_edits <- any(names(edits) %in% names(state$line_targets))
  point_edits <- any(names(edits) %in% names(state$point_targets))
  bar_edits <- any(names(edits) %in% names(state$bar_targets))
  ribbon_edits <- any(names(edits) %in% names(state$ribbon_targets))
  if (reflow) {
    geometry_gids <- names(state$text_targets)
  } else {
    geometry_gids <- names(Filter(function(edit) any(names(edit) %in% names(ggplot_face_options)), edits))
  }
  if (!is.null(measured)) {
    if (length(measured$text) != length(state$text_targets)) {
      stop("The native R text could not be measured in the PNG.")
    }
  } else if (reflow || length(geometry_gids) || line_edits || point_edits || bar_edits || ribbon_edits) {
    measured <- with_svg_frame(state$frame_pt, function(capture) {
      grid::grid.newpage()
      scene <- ggplot_table_for(state, edits)
      grid::grid.draw(scene$table)
      list(text = measure_ggplot_targets(scene$text[geometry_gids], state$frame_pt),
           legends = if (reflow) measure_ggplot_legends(scene$legends, state$frame_pt),
           axes = scene$axes,
           axes_bbox = if (reflow) measure_ggplot_axes(scene$table, state$axes_target, state$frame_pt),
           colour_scales = if (reflow && length(state$colour_scales)) {
             measure_ggplot_colour_scales(scene$table, state$colour_scales, state$frame_pt)
           },
           tick_values = scene$tick_values,
           scale_stops = scene$stops,
           points = if (reflow || point_edits) {
             measure_ggplot_points(scene$table, scene$point_targets, state$frame_pt, !is.null(scene$axes))
           },
           bars = if (reflow || bar_edits) {
             measure_ggplot_bars(scene$table, scene$bar_targets, state$frame_pt, !is.null(scene$axes))
           },
           ribbons = if (reflow || ribbon_edits) {
             measure_ggplot_ribbons(scene$table, scene$ribbon_targets, state$frame_pt)
           },
           lines = if (reflow || line_edits) {
             measure_ggplot_lines(scene$table, scene$line_targets, state$frame_pt)
           })
    })$value
    if (length(measured$text) != length(geometry_gids)) {
      stop("The edited native R text could not be measured.")
    }
  }
  elements <- lapply(names(state$text_targets), function(gid) {
    target <- state$text_targets[[gid]]
    edit <- edits[[gid]]
    tick <- state$tick_targets[[gid]]
    patch <- ggplot_text_edit(edit, tick)
    color <- if (is.null(patch$color)) target$color else patch$color
    painted <- ggplot_text_paint(target, patch)
    face <- painted$face
    bbox <- if (is.null(measured$text[[gid]])) target$bbox else measured$text[[gid]]$bbox
    editable <- list(list(prop = "color", type = "color", value = color))
    if (!is.null(tick)) {
      field <- list(prop = "format", type = "enum", value = if (is.null(edit$format)) "auto" else edit$format,
                    options = as.list(ggplot_number_formats), strict_options = TRUE)
      if (!is.null(edit$format)) field$value_original <- "auto"
      editable[[length(editable) + 1L]] <- field
      if (!is.null(tick$spacing)) {
        mode <- ggplot_major_tick_mode(edit)
        default_step <- tick$spacing$step
        mode_field <- list(prop = "major_mode", type = "enum", value = mode,
                           options = as.list(ggplot_major_tick_modes(tick$spacing)),
                           strict_options = TRUE)
        if (!is.null(edit$major_mode)) mode_field$value_original <- "auto"
        editable[[length(editable) + 1L]] <- mode_field
        if (!is.null(default_step)) {
          step_field <- list(prop = "major_step", type = "number",
                             value = if (is.null(edit$major_step)) default_step else edit$major_step,
                             min = 0, step = default_step / 10)
          if (!is.null(edit$major_step)) step_field$value_original <- default_step
          editable[[length(editable) + 1L]] <- step_field
        }
        values <- measured$tick_values[[gid]]
        if (is.null(values)) values <- tick$values
        if (mode == "fixed" && length(edit$major_values)) values <- ggplot_fixed_tick_values(edit$major_values)
        fixed_field <- list(prop = "major_values", type = "number_list", value = as.list(values))
        if (!is.null(edit$major_values)) fixed_field$value_original <- as.list(tick$values)
        editable[[length(editable) + 1L]] <- fixed_field
        editable <- c(editable, ggplot_minor_tick_fields(tick$spacing$minor, edit))
      }
    }
    for (prop in names(face)) {
      field <- list(prop = prop, type = "enum", value = face[[prop]],
                    options = as.list(ggplot_face_options[[prop]]), strict_options = TRUE)
      if (!is.null(patch[[prop]])) field$value_original <- target$face[[prop]]
      editable[[length(editable) + 1L]] <- field
    }
    family_original <- ggplot_title_family(target)
    if (!is.null(family_original)) {
      requested <- patch$fontfamily
      family <- list(prop = "fontfamily", type = "enum",
                     value = if (is.null(requested)) family_original else requested,
                     options = as.list(unique(c(family_original, names(target$title_fonts)))),
                     strict_options = TRUE)
      if (!is.null(requested)) family$value_original <- family_original
      editable[[length(editable) + 1L]] <- family
    }
    if (!is.null(target$title_dimension) || !is.null(tick$size)) {
      native_size <- if (is.null(tick$size)) target$paint$fontsize * ggplot_text_cex(target$paint) else tick$size
      value_original <- round(native_size, 2)
      fontsize <- if (is.null(edit$fontsize)) value_original else round(edit$fontsize, 2)
      field <- list(prop = "fontsize", type = "number", value = fontsize,
                    min = 3, max = 200, step = 0.5, unit = "pt")
      if (!is.null(edit$fontsize)) field$value_original <- value_original
      editable[[length(editable) + 1L]] <- field
    }
    element <- list(
      gid = gid, role = target$role, label = target$label,
      bbox = as.list(bbox), draggable = FALSE, editable = editable
    )
    if (!is.null(target$legend_title)) element$legend_title <- target$legend_title
    if (!is.null(target$tick_labels)) element$tick_labels <- target$tick_labels
    if (identical(tick$kind, "axis") && !is.null(state$axes_target)) {
      params <- state$built$layout$panel_params[[1L]]$guides$params[[tick$index]]
      if (length(params$aesthetic) == 1L && params$aesthetic %in% c("x", "y")) {
        element$tick_host <- list(axes_gid = ggplot_axes_gid, axis = params$aesthetic)
      }
    }
    if (target$role == "legend_text") {
      host <- sub("^(legend\\.[^.]+)\\..*$", "\\1", gid)
      if (host %in% legend_gids) element$drag_host <- host
    }
    element
  })
  legends <- lapply(legend_gids, function(gid) {
    target <- state$legend_targets[[gid]]
    bbox <- if (is.null(measured$legends)) target$bbox else measured$legends[[gid]]
    if (is.null(bbox)) stop("The edited native R legend could not be measured.")
    list(gid = gid, role = "legend", label = "\u56fe\u4f8b", bbox = as.list(bbox),
         draggable = TRUE, drag_prop = "loc_frac", anchor = as.list(c(bbox[[1L]], bbox[[2L]] + bbox[[4L]])),
         editable = list(), legend_context = target$context)
  })
  lines <- lapply(names(state$line_targets), function(gid) {
    target <- state$line_targets[[gid]]
    patch <- edits[[gid]]
    geometry <- if (is.null(measured$lines)) state$line_geometry[[gid]] else measured$lines[[gid]]
    if (is.null(geometry)) stop("The native R curve could not be measured.")
    if (target$interval) {
      original <- list(linewidth = target$width)
      if (!is.null(target$color)) original <- c(list(color = target$color), original)
      if (!is.null(target$alpha)) original$alpha <- target$alpha / 255
      current <- original
      if (!is.null(target$caps)) {
        original$capsize <- state$line_geometry[[gid]]$capsize
        original$cap_thickness <- target$width
        current$capsize <- geometry$capsize
        current$cap_thickness <- if (is.null(patch$linewidth)) target$width else patch$linewidth
      }
      current[names(patch)] <- patch
      geometry$capsize <- NULL
      return(c(list(gid = gid, role = "errorbar",
                    label = ggplot_layer_label("\u8bef\u5dee\u68d2", target), draggable = FALSE,
                    editable = list(), errorbar_series = list(
                      context = target$context, current = current,
                      original = original, edited = as.list(names(patch)))), geometry))
    }
    width <- list(prop = "linewidth", type = "number", value = geometry$geometry$stroke_pt,
                  min = 0.1, max = 20, step = 0.1, unit = "pt")
    if (!is.null(patch$linewidth)) width$value_original <- target$width
    style <- list(prop = "linestyle", type = "enum",
                  value = if (is.null(patch$linestyle)) target$style else patch$linestyle,
                  options = as.list(names(ggplot_line_types)), strict_options = TRUE)
    if (!is.null(patch$linestyle)) style$value_original <- target$style
    editable <- list(width, style)
    if (!is.null(target$color)) {
      color <- list(prop = "color", type = "color",
                    value = if (is.null(patch$color)) target$color else patch$color)
      if (!is.null(patch$color)) color$value_original <- target$color
      editable <- c(editable, list(color))
    }
    c(list(gid = gid, role = "line", label = ggplot_layer_label("\u66f2\u7ebf", target),
           draggable = FALSE, editable = editable, line_context = target$context),
      geometry)
  })
  colour_scales <- if (length(state$colour_scales)) {
    ggplot_colour_scale_elements(state, edits, measured$colour_scales, measured$scale_stops)
  }
  axes_bbox <- if (is.null(measured$axes_bbox)) state$axes_bbox else measured$axes_bbox
  axes <- ggplot_axes_element(state$axes_target, measured$axes, axes_bbox, edits[[ggplot_axes_gid]])
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72),
       elements = c(axes, legends, elements, lines,
                    ggplot_point_elements(state, edits, measured$points),
                    ggplot_bar_elements(state, edits, measured$bars),
                    ggplot_ribbon_elements(state, edits, measured$ribbons), colour_scales))
}
