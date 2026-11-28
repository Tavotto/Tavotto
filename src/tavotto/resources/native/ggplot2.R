# The adapter receives a plot from an already authorized R invocation. Building
# the layout and applying edits are separate so a paint change never reruns a stat.

close_ggplot_device <- function(device, previous) {
  if (device %in% grDevices::dev.list()) grDevices::dev.off(which = device)
  if (previous %in% grDevices::dev.list() && previous != grDevices::dev.cur()) {
    grDevices::dev.set(previous)
  }
  invisible(NULL)
}

with_svg_frame <- function(frame_pt, draw) {
  if (!is.numeric(frame_pt) || length(frame_pt) != 2L ||
      any(!is.finite(frame_pt)) || any(frame_pt <= 0)) {
    stop("The SVG frame needs two positive dimensions in points.")
  }
  previous <- grDevices::dev.cur()
  capture <- svglite::svgstring(width = frame_pt[[1L]] / 72,
                               height = frame_pt[[2L]] / 72)
  device <- grDevices::dev.cur()
  on.exit(close_ggplot_device(device, previous))
  value <- draw(capture)
  close_ggplot_device(device, previous)
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

ggplot_text_target <- function(table, slots, role, title_child = NULL, group_label = NULL,
                               allow_empty = FALSE) {
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
    if (!is.character(label) || length(label) != 1L || is.na(label) ||
        (!allow_empty && !nzchar(label))) return(NULL)
  } else {
    if ((!is.character(label) && !is.numeric(label)) || !length(label) ||
        anyNA(label) || (!allow_empty && any(!nzchar(label))) || isTRUE(text$check.overlap)) return(NULL)
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
  if ((is.null(group_label) || role == "ticks") && length(font) == 1L && font %in% 1:4) {
    face <- list(weight = if (font %in% c(2L, 4L)) "bold" else "normal",
                 style = if (font %in% c(3L, 4L)) "italic" else "normal")
  }
  edit_path <- if (is.null(title_child)) text$name else grid::gPath(table$name, text$name)
  rotation <- if (role == "ticks" && length(text$rot) == 1L && is.finite(text$rot)) text$rot
  list(slots = slots, title_child = title_child,
       parent_path = paste(c(path, table$name), collapse = "::"),
       child = text$name, edit_path = edit_path,
       label = if (is.null(group_label)) label else group_label,
       tick_labels = if (!is.null(group_label)) as.list(as.character(label)) else NULL,
       paint = text$gp, color = color, face = face, rotation = rotation, role = role)
}


ggplot_axis_ticks <- function(table, axes_targets) {
  targets <- list()
  duplicate <- character()
  facets <- Filter(function(target) !is.null(target$panel), axes_targets)
  if (!length(facets) && sum(grepl("^panel(-[0-9]+-[0-9]+)?$", table$layout$name)) != 1L) return(list())
  panel_slots <- vapply(facets, `[[`, integer(1L), "slot")
  cells <- table$layout[panel_slots, ]
  rownames(cells) <- names(facets)
  for (base in rownames(ggplot_axis_tick_fields)) {
    side <- ggplot_axis_tick_fields[base, ]
    position <- strsplit(base, ".", fixed = TRUE)[[1L]][[3L]]
    slots <- if (length(facets)) which(startsWith(table$layout$name, paste0(side[[1L]], "-"))) else {
      which(table$layout$name == side[[1L]])
    }
    for (slot in slots) {
      panel_gid <- if (length(facets)) ggplot_tick_facet_owner(table$layout[slot, ], cells, position) else {
        ggplot_axes_gid
      }
      # Visible labels can be styled even on facet axes with a fixed or discrete scale.
      if (is.null(panel_gid)) next
      axis <- table$grobs[[slot]]$children[["axis"]]
      if (!inherits(axis, "gtable")) next
      labels <- which(vapply(axis$grobs, inherits, logical(1L), "titleGrob"))
      if (length(labels) != 1L) next
      label <- paste0(side[[2L]], "\u8f74\u523b\u5ea6\u6587\u5b57")
      target <- ggplot_text_target(table, list(slot, "axis", labels), "ticks", group_label = label)
      if (is.null(target)) next
      gid <- if (length(facets)) paste0(base, ".", panel_gid) else base
      if (!is.null(targets[[gid]])) duplicate <- c(duplicate, gid)
      target$panel <- facets[[panel_gid]]$panel %||% 1L
      target$axes_gid <- panel_gid
      target$position <- position
      target$axis_base <- base
      if (length(facets)) {
        target$label <- paste(target$label, paste(facets[[panel_gid]]$facet, collapse = " / "), sep = " \u00b7 ")
      }
      targets[[gid]] <- target
    }
  }
  targets[setdiff(names(targets), duplicate)]
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

ggplot_colourbar_key_values <- function(params, scale) {
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
  values <- endpoints[[1L]] + (params$key$.value - inset) / (1 - 2 * inset) * diff(endpoints)
  if (!is.null(scale)) {
    tryCatch(scale$get_transformation()$inverse(values), error = function(error) NULL)
  }
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
  } else ggplot_transformed_locator_step(view, 5)
  minor <- ggplot_minor_tick_defaults(built, params, panel, axis)
  list(axis = axis, step = if (is_positive_ggplot_number(step)) step, minor = minor)
}

ggplot_axis_key_values <- function(panel, params) {
  key <- ggplot_major_axis_key(params$key)
  if (!length(key$.value)) return(numeric())
  transform <- panel[[params$aesthetic]]$scale$get_transformation()
  transform$inverse(key$.value)
}

ggplot_mark_fields <- list(
  length = c(min = 0, max = 20, step = 0.5),
  width = c(min = 0.1, max = 10, step = 0.1)
)
ggplot_axis_mark_props <- c(names(ggplot_mark_fields), paste0("minor_", names(ggplot_mark_fields)))
ggplot_axis_tick_directions <- c("out", "in", "inout")

ggplot_axis_marks <- function(guide, params, theme, spacing) {
  resolved <- guide$setup_elements(guide$setup_params(params), guide$elements, theme)
  describe <- function(line, length_unit) {
    if (!inherits(line, "element_line") || !is_positive_ggplot_number(line$linewidth) ||
        !grid::is.unit(length_unit) || length(length_unit) != 1L ||
        !grid::unitType(length_unit) %in% c("inches", "cm", "mm", "points", "bigpts", "picas")) {
      return(NULL)
    }
    size <- grid::convertUnit(length_unit, "bigpts", valueOnly = TRUE)
    if (!is.finite(size)) return(NULL)
    list(line = line, size = size, length = abs(size),
         width = line$linewidth * ggplot2::.pt * ggplot_lwd_pt)
  }
  major <- describe(resolved$ticks, resolved$major_length)
  minor <- describe(resolved$minor, resolved$minor_length)
  # An authored guide can draw the two tiers on different sides. Its
  # mixed setting has no single direction in the shared editor.
  direction <- if (!is.null(major) && !inherits(major$line$arrow, "arrow") &&
                   (!isTRUE(params$minor.ticks) || is.null(minor) ||
                    (!inherits(minor$line$arrow, "arrow") && (minor$size < 0) == (major$size < 0)))) {
    if (major$size < 0) "in" else "out"
  }
  list(major = major, minor = minor, direction = direction,
       minor_editable = isTRUE(params$minor.ticks) || !is.null(spacing$minor))
}

ggplot_axis_is_secondary <- function(panel, params) {
  axis <- params$aesthetic
  length(axis) == 1L && axis %in% ggplot_axis_names &&
    !is.null(panel[[axis]]$position) && !identical(params$position, panel[[axis]]$position)
}

ggplot_tick_targets <- function(built, targets, colourbar_titles, axes_targets, panels) {
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    if (is.null(target$tick_labels)) next
    if (target$role == "ticks") {
      position <- target$position
      panel <- built$layout$panel_params[[target$panel]]
      guides <- panel$guides
      matches <- which(vapply(guides$params, function(p) identical(p$position, position), logical(1L)))
      if (length(matches) == 1L && identical(class(guides$guides[[matches]])[[1L]], "GuideAxis")) {
        params <- guides$params[[matches]]
        key <- ggplot_major_axis_key(params$key)
        numeric <- target$axes_gid == ggplot_axes_gid ||
          (!is.null(axes_targets[[target$axes_gid]]$groups[[params$aesthetic]]) &&
           !ggplot_axis_is_secondary(panel, params))
        values <- if (numeric) ggplot_numeric_key(key, target)
        if (numeric && is.null(values) && identical(params$aesthetic, substr(gid, 6L, 6L)) &&
            isTRUE(panel[[params$aesthetic]]$scale$get_transformation()$name %in%
                   setdiff(names(ggplot_axis_modes), "identity"))) {
          values <- ggplot_numeric_key(key, target, ggplot_axis_key_values(panel, params))
        }
        size <- target$paint$fontsize * ggplot_text_cex(target$paint)
        angle_from_theme <- is.null(params$angle) || inherits(params$angle, "waiver")
        spacing <- if (!is.null(values)) ggplot_axis_spacing(built, panel, params, values)
        suffix <- substring(target$axis_base, 6L, nchar(target$axis_base) - 6L)
        marks <- if (identical(class(built$layout$coord)[[1L]], "CoordCartesian") &&
                     (is.null(params$theme_suffix) || identical(params$theme_suffix, suffix))) {
          ggplot_axis_marks(guides$guides[[matches]], params, built$plot$theme, spacing)
        }
        result[[gid]] <- list(
          kind = "axis", index = matches, panel = target$panel, position = position,
          axes_gid = target$axes_gid, axis = params$aesthetic, values = values,
          spacing = spacing, marks = marks,
          size = if (is_positive_ggplot_number(size)) size,
          rotation = if (angle_from_theme) target$rotation
        )
      }
    } else if (target$role == "legend_text" && target$legend_title %in% colourbar_titles) {
      index <- match(target$legend_title, unname(colourbar_titles))
      key <- names(colourbar_titles)[[index]]
      params <- built$plot$guides$params[[key]]
      scale <- ggplot_colourbar_scale(built$plot$scales, params$aesthetic)
      values <- ggplot_numeric_key(params$key, target, ggplot_colourbar_key_values(params, scale))
      if (!is.null(values)) {
        result[[gid]] <- list(kind = "colourbar", index = key, values = values)
      }
    }
  }
  members <- list()
  cells <- built$layout$layout
  for (gid in names(result)) {
    tick <- result[[gid]]
    if (!is.null(tick$spacing)) {
      groups <- cells[[paste0("SCALE_", toupper(tick$axis))]]
      native_group <- groups[as.integer(cells$PANEL) == tick$panel]
      if (length(native_group) == 1L && !is.na(native_group) &&
          (length(unique(groups)) == 1L || length(panels$facets))) {
        result[[gid]]$locator_axis <- tick$axis
        result[[gid]]$locator_scope <- paste(tick$axis, native_group, sep = "/")
        if (length(unique(groups)) > 1L) {
          owner <- which(groups == native_group)[[1L]]
          result[[gid]]$locator_facet <- lapply(cells[owner, panels$facets, drop = FALSE], as.character)
        }
      }
    }
    axes <- axes_targets[[tick$axes_gid %||% ggplot_axes_gid]]
    group <- if (length(tick$axis) == 1L) axes$groups[[tick$axis]]
    if (is.null(group) || is.null(tick$values)) next
    scope <- paste(tick$axis, group, sep = "/")
    result[[gid]]$scope <- scope
    members[[scope]] <- c(members[[scope]], gid)
  }
  for (peers in members) {
    if (length(peers) < 2L) next
    result[[peers[[1L]]]]$links <- peers[-1L]
    for (gid in peers[-1L]) result[[gid]]$links <- peers[[1L]]
  }
  result
}

ggplot_major_tick_props <- c("major_mode", "major_step", "major_values")
ggplot_minor_tick_props <- c("minor_visible", "minor_mode", "minor_step", "minor_format")
ggplot_locator_props <- c("format", ggplot_major_tick_props)
ggplot_tick_props <- c(ggplot_locator_props, ggplot_minor_tick_props)

ggplot_captured_locators <- function(targets, cells, global, faceted) {
  result <- list()
  for (target in targets) {
    scope <- target$locator_scope
    if (!is.null(scope) && !is.null(global[[target$locator_axis]])) {
      result[[scope]] <- global[[target$locator_axis]]
    }
  }
  for (recipe in faceted) {
    group <- ggplot_facet_group_id(cells, recipe$axis, recipe$facet)
    if (is.null(group)) next
    scope <- paste(recipe$axis, group, sep = "/")
    result[[scope]] <- utils::modifyList(result[[scope]] %||% list(), recipe$style)
  }
  result
}

ggplot_authored_locator_edits <- function(state, edits) {
  for (scope in names(state$authored_locators)) {
    gids <- names(Filter(function(tick) identical(tick$locator_scope, scope), state$tick_targets))
    if (!length(gids)) stop("The R source locator no longer matches the rendered axis.")
    supplied <- unique(unlist(lapply(edits[gids], names), use.names = FALSE))
    stored <- state$authored_locators[[scope]]
    gid <- gids[[1L]]
    edits[[gid]] <- c(stored[setdiff(names(stored), supplied)], edits[[gid]])
  }
  edits
}

ggplot_format_guide <- function(params, format, values) {
  if (!is.character(format) || length(format) != 1L || !format %in% ggplot_number_formats) {
    stop("Invalid native R tick number format.")
  }
  if (format != "auto") {
    labels <- ggplot_number_labels(format, values)
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

ggplot_major_tick_modes <- function(spacing) {
  c("auto", if (!is.null(spacing$step)) "step", "fixed")
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
      positions <- ggplot_uniform_breaks(transform$inverse(transformed_range), step)
      values <- ggplot_usable_fixed_ticks(positions, transform$name)
      breaks <- transform$transform(values)
    } else if (supplied) {
      # Crop ticks to the visible view without expanding or retraining the axis.
      # Compare in scale coordinates so the guide and grid share the same marks.
      values <- ggplot_usable_fixed_ticks(fixed, transform$name)
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
  params <- ggplot_format_minor_labels(panel, params, edit$minor_format)
  list(panel = panel, params = ggplot_format_guide(params, format, values))
}



ggplot_facet_tick_edits <- function(state, edits) {
  groups <- list()
  for (gid in names(edits)) {
    target <- state$tick_targets[[gid]]
    if (is.null(target$scope)) next
    fields <- edits[[gid]][intersect(names(edits[[gid]]), ggplot_tick_props)]
    current <- groups[[target$scope]]
    for (prop in names(fields)) {
      if (!is.null(current[[prop]]) && !identical(current[[prop]], fields[[prop]])) {
        stop("Shared native R locators cannot take conflicting values.")
      }
      current[[prop]] <- fields[[prop]]
    }
    groups[[target$scope]] <- current
  }
  groups
}

ggplot_facet_tick_layout <- function(state, panels, edits, coord, axis_changes) {
  values <- list()
  changed <- hidden <- character()
  tick_targets <- Filter(function(tick) identical(tick$kind, "axis") && tick$axes_gid != ggplot_axes_gid,
                         state$tick_targets)
  if (!length(tick_targets)) return(list(panels = panels))
  owners <- vapply(tick_targets, function(tick) paste(tick$axes_gid, tick$position, sep = "/"), character(1L))
  gids <- as.list(stats::setNames(names(owners), owners))
  for (axes_gid in names(state$axes_targets)) {
    axes <- state$axes_targets[[axes_gid]]
    if (is.null(axes$panel)) next
    panel <- panels[[axes$panel]]
    guides <- panel$guides
    params <- guides$params
    original <- state$built$layout$panel_params[[axes$panel]]
    for (index in seq_along(params)) {
      param <- params[[index]]
      axis <- param$aesthetic
      if (length(axis) != 1L || !axis %in% ggplot_axis_names) next
      gid <- gids[[paste(axes_gid, param$position, sep = "/")]]
      group <- axes$groups[[axis]]
      projected <- !is.null(group) && length(axis_changes[[axis]][[group]]) > 0L
      patch <- if (!is.null(group) && !ggplot_axis_is_secondary(panel, param)) {
        edits[[paste(axis, group, sep = "/")]]
      }
      if (!is.null(patch)) {
        target <- if (!is.null(gid)) tick_targets[[gid]] else {
          initial <- original$guides$params[[index]]
          baseline <- ggplot_axis_key_values(original, initial)
          list(values = baseline, spacing = ggplot_axis_spacing(state$built, original, initial, baseline))
        }
        result <- ggplot_edit_axis_guide(panel, param, target, patch, coord, projected)
        panel <- result$panel
        params[[index]] <- result$params
      }
      if (!is.null(gid) && (projected || length(patch))) {
        changed <- c(changed, gid)
        values[[gid]] <- ggplot_axis_key_values(panel, params[[index]])
        if (!length(values[[gid]])) hidden <- c(hidden, gid)
      }
    }
    panel$guides <- ggplot_clone(guides, params = params,
                                guides = Map(ggplot_guide_with_minor_labels, guides$guides, params))
    panels[[axes$panel]] <- panel
  }
  list(panels = panels, values = values, changed = changed, hidden = hidden)
}


ggplot_style_axis_guide <- function(guide, params, gid, style) {
  name <- ggplot_axis_tick_theme_names[[gid]]
  elements <- style$marks
  if (identical(style$visible, FALSE)) {
    elements[[name]] <- ggplot2::element_blank()
  } else if (any(lengths(style[c("size", "face", "rotation", "family")]) > 0L)) {
    elements[[name]] <- ggplot2::element_text(
      size = style$size, face = style$face, angle = style$rotation, family = style$family
    )
  }
  patch <- do.call(ggplot2::theme, elements)
  params$theme <- if (is.null(params$theme)) patch else params$theme + patch
  if (identical(style$direction, "inout")) guide <- ggplot_centered_axis_guide()
  list(guide = ggplot_guide_with_minor_labels(guide, params), params = params)
}

ggplot_reflow_facet_guides <- function(table, layout, theme, text_targets, tick_targets, styles) {
  rows <- cols <- integer()
  for (gid in names(styles)) {
    text <- text_targets[[gid]]
    tick <- tick_targets[[gid]]
    slot <- text$slots[[1L]]
    position <- tick$position
    cell <- table$layout[slot, ]
    horizontal <- position %in% c("top", "bottom")
    if ((horizontal && cell$t != cell$b) || (!horizontal && cell$l != cell$r)) {
      stop("The native R facet guide no longer occupies its captured layout track.")
    }
    view <- layout$panel_params[[tick$panel]]
    guides <- view$guides
    index <- which(vapply(guides$params, function(p) identical(p$position, position), logical(1L)))
    if (length(index) != 1L) stop("The native R facet guide has changed.")
    styled <- ggplot_style_axis_guide(guides$guides[[index]], guides$params[[index]], text$axis_base,
                                      styles[[gid]])
    guide_list <- guides$guides
    params <- guides$params
    guide_list[[index]] <- styled$guide
    params[[index]] <- styled$params
    view$guides <- ggplot_clone(guides, guides = guide_list, params = params)
    draw <- if (horizontal) layout$coord$render_axis_h else layout$coord$render_axis_v
    table$grobs[[slot]] <- draw(view, theme)[[position]]
    if (horizontal) rows <- c(rows, cell$t) else cols <- c(cols, cell$l)
  }
  ggplot_resize_drawn_axis_tracks(table, rows, cols)
}

ggplot_guide_table <- function(state, edits, colour_edits, axis_styles, guide_strokes, axis_changes,
                               grid_edits, theme_edits, aspect, side_edits, facet_changes, point_edits, member_edits,
                               titles, capture_order = FALSE) {
  built <- state$built
  plot <- ggplot_relabel_titles(built$plot, titles)
  axes_target <- state$axes_targets[[ggplot_axes_gid]]
  if (length(grid_edits)) plot$theme <- ggplot_grid_theme(plot$theme, axes_target$grid, grid_edits)
  if (length(theme_edits)) plot$theme <- plot$theme + do.call(ggplot2::theme, theme_edits)
  guide_params <- plot$guides$params
  remapped <- if (length(colour_edits)) ggplot_remap_colour_scales(state, colour_edits, guide_params)
  if (!is.null(remapped)) guide_params <- remapped$guides
  guide_params <- ggplot_style_point_keys(guide_params, state$point_targets, point_edits)
  if (!is.null(remapped)) built$data <- remapped$data
  redraw <- length(axis_changes) || length(facet_changes)
  if (redraw) {
    for (index in names(state$series_categories)) {
      i <- as.integer(index)
      categories <- state$series_categories[[index]]
      attr(built$data[[i]], "tavotto_series_categories") <- categories
      if (isTRUE(plot$layers[[i]]$tavotto_curve_source)) {
        attr(built$data[[i]], "tavotto_curve_categories") <- categories
      }
    }
  }
  if (length(axis_changes)) built <- ggplot_project_axes(built, axes_target, axis_changes)
  if (length(facet_changes)) built <- ggplot_project_facet_axes(built, state$facet_axis_groups, facet_changes)
  layout <- ggplot_clone(built$layout)
  layout$coord <- ggplot_relabel_coordinates(layout$coord, titles)
  if (!is.null(aspect)) layout <- ggplot_edit_aspect(layout, axes_target, aspect)
  panels <- layout$panel_params
  for (gid in ggplot_series_edit_order(names(guide_strokes), state$line_targets)) {
    target <- state$line_targets[[gid]]
    style <- ggplot_line_style(target, guide_strokes[[gid]])
    for (index in target$guide_keys) {
      key <- guide_params[[index]]$decor[[target$key_name]]$data
      if (is.null(target$group)) {
        if (!is.null(style$lwd)) key$linewidth <- style$lwd / ggplot2::.pt
        if (!is.null(style$lty)) key$linetype <- style$lty
      } else key <- ggplot_curve_guide_style(key, target$group$index, guide_strokes[[gid]])
      guide_params[[index]]$decor[[target$key_name]]$data <- key
    }
  }
  if (length(panels) == 1L) {
    axis_params <- panels[[1L]]$guides$params
    axis_guides <- panels[[1L]]$guides$guides
  }
  hidden_ticks <- character()
  for (gid in names(edits)) {
    target <- state$tick_targets[[gid]]
    if (is.null(target$values)) stop("The requested native R tick formatting is unavailable.")
    index <- target$index
    edit <- edits[[gid]]
    if (target$kind == "axis") {
      if (target$axes_gid != ggplot_axes_gid) {
        if (is.null(target$scope)) stop("The native R facet tick group is unavailable.")
        next
      }
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
  single <- length(panels) == 1L
  for (gid in if (single) names(axis_styles) else character()) {
    index <- state$tick_targets[[gid]]$index
    style <- axis_styles[[gid]]
    styled <- ggplot_style_axis_guide(axis_guides[[index]], axis_params[[index]],
                                      state$text_targets[[gid]]$axis_base, style)
    axis_params[[index]] <- styled$params
    axis_guides[[index]] <- styled$guide
  }
  hidden_ticks <- union(hidden_ticks, names(Filter(function(style) identical(style$visible, FALSE), axis_styles)))
  if (length(panels) == 1L) {
    if (length(side_edits)) {
      sides <- ggplot_apply_tick_sides(axes_target$tick_sides, side_edits, axis_guides,
                                       axis_params, plot$theme)
      axis_params <- sides$params
      axis_guides <- sides$guides
    }
    axis_guides <- Map(ggplot_guide_with_minor_labels, axis_guides, axis_params)
    panels[[1L]]$guides <- ggplot_clone(panels[[1L]]$guides, params = axis_params, guides = axis_guides)
    layout$panel_params <- panels
  }
  facet_ticks <- ggplot_facet_tick_layout(
    state, panels, ggplot_facet_tick_edits(state, edits), layout$coord, facet_changes
  )
  source <- ggplot_source_tick_edits(facet_ticks$panels, layout$layout,
                                     state$authored_tick_recipes, state$tick_member_scopes)
  default_labels <- ggplot_relabel_tick_members(facet_ticks$panels, source$scopes, source$edits)
  relabelled <- ggplot_relabel_tick_members(default_labels$panels, state$tick_member_scopes, member_edits)
  changed_labels <- union(names(source$edits), names(member_edits))
  layout$panel_params <- relabelled$panels
  plot$guides <- ggplot_colourbar_guides(
    ggplot_clone(plot$guides, params = guide_params), plot$scales, remapped$extensions, remapped$outlines
  )
  captured <- new.env(parent = emptyenv())
  if (redraw || capture_order) captured$grobs <- vector("list", length(plot$layers))
  plot$layers <- Map(function(layer, grobs, index) {
    layer <- if (redraw || index %in% remapped$changed) {
      ggplot_mirror_raster_layer(layer, layout$coord)
    } else ggplot_frozen_layer(layer, grobs)
    if (redraw || capture_order) ggplot_watch_layer(layer, index, captured) else layer
  }, plot$layers, state$layer_grobs, seq_along(plot$layers))
  built$layout <- layout
  built$plot <- plot
  axes <- if (redraw) lapply(names(state$axes_targets), function(gid) {
    target <- state$axes_targets[[gid]]
    changes <- if (gid == ggplot_axes_gid) axis_changes else ggplot_facet_axis_changes(target, facet_changes)
    if (length(changes)) ggplot_axes_values(layout, target, changes)
  }) |> stats::setNames(names(state$axes_targets))
  tick_values <- lapply(Filter(function(tick) !is.null(tick$spacing) && tick$axes_gid == ggplot_axes_gid,
                              state$tick_targets), function(tick) {
    ggplot_axis_key_values(panels[[1L]], axis_params[[tick$index]])
  })
  tick_values <- c(tick_values, facet_ticks$values)
  table <- ggplot2::ggplot_gtable(built)
  if (!single) {
    for (gid in setdiff(changed_labels, names(axis_styles))) {
      axis_styles[[gid]] <- list(marks = list())
    }
  }
  if (!single && length(axis_styles)) {
    table <- ggplot_reflow_facet_guides(table, layout, plot$theme, state$text_targets,
                                       state$tick_targets, axis_styles)
  }
  table <- ggplot_hide_colourbars(table, remapped$hidden)
  hidden_colour_text <- unlist(lapply(remapped$hidden, function(target) c(target$title_gid, target$ticks_gid)),
                               use.names = FALSE)
  list(table = table, layout = layout, stops = remapped$stops,
       guide_ticks = remapped$guide_ticks, axes = axes, layer_grobs = captured$grobs,
       tick_values = tick_values,
       hidden_ticks = c(hidden_ticks, facet_ticks$hidden, hidden_colour_text,
                        setdiff(default_labels$hidden, names(member_edits)), relabelled$hidden),
       changed_ticks = union(facet_ticks$changed, changed_labels), member_groups = changed_labels,
       label_originals = utils::modifyList(default_labels$originals, relabelled$originals),
       title_changes = remapped$title_changes)
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
      context = context, source_titles = ggplot_legend_box_titles(box)
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

ggplot_drawn_text <- function(drawn, target) {
  row <- which(drawn$name == target$child & drawn$gPath == target$parent_path)
  if (length(row) != 1L) return(NULL)
  # Nested guides can insert viewports that are not part of their grob paths.
  viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
  if (length(viewport) < 2L || viewport[[1L]] != "ROOT") return(NULL)
  grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
  grid::grid.get(grid::gPath(target$parent_path, target$child), strict = TRUE)
}

measure_ggplot_text <- function(drawn, target, frame_pt) {
  on.exit(grid::upViewport(0))
  text <- ggplot_drawn_text(drawn, target)
  if (!is.null(text)) ggplot_text_grob_bounds(text, frame_pt)
}

ggplot_text_grob_bounds <- function(text, frame_pt) {
  lower <- grid::deviceLoc(grid::grobX(text, 180), grid::grobY(text, 270), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::grobX(text, 0), grid::grobY(text, 90), valueOnly = TRUE)
  ggplot_bbox(lower, upper, frame_pt)
}

ggplot_bbox <- function(lower, upper, frame_pt) {
  frame_in <- frame_pt / 72
  c(lower$x / frame_in[[1L]], 1 - upper$y / frame_in[[2L]],
    (upper$x - lower$x) / frame_in[[1L]], (upper$y - lower$y) / frame_in[[2L]])
}

ggplot_text_targets <- function(table, frame_pt, colourbar_titles, axes_targets, coord) {
  targets <- list()
  families <- ggplot_installed_text_fonts()
  for (gid in names(ggplot_text_slots)) {
    slot <- which(table$layout$name == ggplot_text_slots[[gid]])
    if (length(slot) != 1L) next
    role <- if (gid == "plot.title") "title" else if (startsWith(gid, "axis.")) "axis_label" else "text"
    editable_text <- inherits(table$grobs[[slot]], "titleGrob") &&
      (role != "axis_label" || identical(class(coord)[[1L]], "CoordCartesian"))
    target <- ggplot_text_target(table, slot, role, allow_empty = editable_text)
    if (is.null(target)) next
    if (editable_text) {
      target$text_slot <- ggplot_text_slots[[gid]]
      target$hidden <- !nzchar(target$label)
    }
    if (inherits(table$grobs[[slot]], "titleGrob") &&
        is_positive_ggplot_number(target$paint$fontsize) &&
        is_positive_ggplot_number(ggplot_text_cex(target$paint))) {
      target$title_dimension <- if (startsWith(gid, "axis.y.")) "width" else "height"
      target$font_families <- families
    }
    targets[[gid]] <- target
  }
  ticks <- lapply(ggplot_axis_ticks(table, axes_targets), function(target) {
    target$font_families <- families
    target
  })
  targets <- c(targets, ggplot_legend_targets(table, colourbar_titles), ggplot_facet_labels(table), ticks)
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
  members <- lapply(table$grobs[slots], function(grob) names(grob$children))
  drawn_names <- unlist(members, use.names = FALSE)
  placements <- data.frame(grob = drawn_names, slot = rep(slots, lengths(members)),
                           child = sequence(lengths(members)))
  placements <- placements[!(duplicated(drawn_names) | duplicated(drawn_names, fromLast = TRUE)), , drop = FALSE]
  list(placements = placements, cells = cells, facets = facets, single = single)
}

ggplot_layer_priorities <- function(panels, layer_grobs) {
  if (is.null(panels) || !length(layer_grobs)) return(list())
  priorities <- unlist(Map(function(grobs, index) {
    drawn <- vapply(grobs, ggplot_drawn_layer_name, character(1L))
    drawn <- drawn[!is.na(drawn)]
    stats::setNames(rep(index, length(drawn)), drawn)
  }, unname(layer_grobs), seq_along(layer_grobs)))
  placements <- panels$placements
  matches <- match(placements$grob, names(priorities))
  found <- !is.na(matches)
  split(priorities[matches[found]], placements$slot[found])
}

ggplot_layer_order_targets <- function(table, priorities, targets) {
  Filter(Negate(is.null), lapply(targets, function(target) {
    if (length(target$parts) || !is.null(target$group) || !is.null(target$cap_rows)) return(NULL)
    slot <- target$slot
    if (is.null(slot) || is.null(target$child)) return(NULL)
    ranks <- priorities[[as.character(slot)]]
    if (length(ranks) < 2L) return(NULL)
    name <- table$grobs[[slot]]$children[[target$child]]$name
    rank <- ranks[match(name, names(ranks))]
    if (length(rank) == 1L && !is.na(rank) && rank == target$index) {
      list(slot = slot, index = target$index, facet = target$facet)
    }
  }))
}

ggplot_layer_order_edits <- function(state, edits) {
  order <- Filter(Negate(is.null), lapply(edits, function(edit) edit[["zorder"]]))
  for (gid in names(order)) {
    value <- order[[gid]]
    if (is.null(state$layer_order_targets[[gid]]) || !is.numeric(value) ||
        length(value) != 1L || !is.finite(value)) {
      stop("The requested native R layer order is unavailable.")
    }
  }
  order
}

ggplot_authored_layer_order <- function(table, panels, recipes) {
  if (is.null(panels) || !length(recipes)) return(list())
  groups <- ggplot_layer_order_recipes(panels$cells, recipes)
  slots <- lapply(names(groups), function(key) {
    row <- match(key, as.character(panels$cells$PANEL))
    slot <- if (panels$single) match("panel", table$layout$name) else {
      ggplot_facet_panel_slots(table, panels$cells[row, ])
    }
    if (is.na(slot)) stop("Tavotto could not find the reordered R facet.")
    as.character(slot)
  })
  stats::setNames(groups, slots)
}

ggplot_layer_paint_orders <- function(table, priorities, slots) {
  orders <- lapply(slots, function(slot) {
    ranks <- priorities[[slot]]
    order <- table$grobs[[as.integer(slot)]]$childrenOrder
    ranks <- ranks[match(order, names(ranks))]
    as.list(unname(ranks[!is.na(ranks)]))
  })
  stats::setNames(orders, slots)
}

ggplot_order_panel_layers <- function(table, priorities, targets, edits, authored) {
  slots <- split(names(edits), vapply(targets[names(edits)], `[[`, integer(1L), "slot"))
  for (slot in names(slots)) {
    changes <- authored[[slot]]
    for (gid in slots[[slot]]) changes[[as.character(targets[[gid]]$index)]] <- edits[[gid]]
    index <- as.integer(slot)
    table$grobs[[index]] <- ggplot_order_layer_children(table$grobs[[index]], priorities[[slot]], changes)
  }
  table
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

ggplot_line_rgb <- function(colours) {
  if (!is.character(colours) || anyNA(colours)) return(NULL)
  channels <- tryCatch(grDevices::col2rgb(colours), error = function(error) NULL)
  if (is.null(channels)) return(NULL)
  unname(grDevices::rgb(channels[1L, ], channels[2L, ], channels[3L, ], maxColorValue = 255))
}

ggplot_line_group_paint <- function(line) {
  ids <- unique(line$id)
  colours <- ggplot_line_rgb(line$gp$col)
  if (length(colours) == length(ids)) colours
}

ggplot_line_group_slots <- function(line, group, part) {
  if (is.null(group$category)) return(which(ggplot_line_group_paint(line) == group$colour))
  if (length(part$stroke_categories) != length(unique(line$id))) {
    stop("The native R curve group changed.")
  }
  which(part$stroke_categories == group$category)
}

ggplot_curve_stroke_categories <- function(line, rows, categories, layout, panel) {
  if (length(categories) != nrow(rows) || anyNA(categories)) return(NULL)
  runs <- ggplot_curve_stroke_rows(line, rows, layout, panel)
  if (is.null(runs)) return(NULL)
  keys <- lapply(runs, function(indices) unique(categories[indices]))
  if (all(lengths(keys) == 1L)) as.integer(unlist(keys, use.names = FALSE))
}

ggplot_curve_group_style <- function(parts, rows, prop, original, shared) {
  styles <- unique(unlist(Map(function(part, slots) {
    styles <- if (shared) part$curve_shared_style else part$curve_style
    values <- styles[[prop]]
    base <- rep_len(original(part), length(part$stroke_alpha))[slots]
    if (is.null(values)) return(base)
    selected <- values[slots]
    selected[is.na(selected)] <- base[is.na(selected)]
    selected
  }, parts, rows), use.names = FALSE))
  if (length(styles) == 1L) styles[[1L]]
}

ggplot_line_group_targets <- function(built, layer, parts, paint, parent_gid, faceted, mapped, mapping,
                                      shared_parent = NULL) {
  target <- parts[[1L]]
  if (length(target$guide_keys) != 1L || is.na(ggplot_axis_layers(list(layer)))) return(list())
  scale <- built$plot$scales$get_scales("colour")
  if (is.null(scale) || !isTRUE(scale$is_discrete())) return(list())
  params <- built$plot$guides$params[[target$guide_keys]]
  if (!identical(params$aesthetic, "colour")) return(list())
  key <- params$key
  labels <- key$.label
  colours <- ggplot_line_rgb(key$colour)
  draws <- params$decor[[target$key_name]]$data$.draw
  measured_categories <- !is.null(mapped) && all(vapply(parts, function(part) {
    !is.null(part$stroke_categories)
  }, logical(1L)))
  if (!is.character(labels) || anyNA(labels) || anyDuplicated(labels) ||
      length(colours) < 2L || (!measured_categories && anyDuplicated(colours)) ||
      length(labels) != length(colours) || any(lengths(paint) == 0L) ||
      !is.logical(draws) || anyNA(draws) || length(draws) != length(colours)) return(list())
  category_ids <- if (measured_categories) match(key$.value, mapped$keys)
  if (measured_categories && anyNA(category_ids)) return(list())
  members <- lapply(seq_along(colours), function(index) {
    lapply(seq_along(parts), function(i) {
      if (measured_categories) which(parts[[i]]$stroke_categories == category_ids[[index]])
      else which(paint[[i]] == colours[[index]])
    })
  })
  visible <- draws & vapply(members, function(rows) any(lengths(rows) > 0L), logical(1L))
  if (!faceted && is.null(shared_parent) && sum(visible) < 2L) return(list())
  result <- list()
  for (index in which(visible)) {
    group <- target
    rows <- members[[index]]
    present <- lengths(rows) > 0L
    group$group <- list(parent = parent_gid, index = index, label = labels[[index]],
                        colour = colours[[index]], category = if (measured_categories) category_ids[[index]],
                        source_value = if (measured_categories) as.character(key$.value[[index]]))
    if (length(shared_parent)) group$group$shared <- paste0(shared_parent, ".group.", index)
    group$visibility_editable <- TRUE
    shared <- is.null(shared_parent)
    group$visible <- ggplot_curve_group_style(parts, rows, "visible", function(part) TRUE, shared)
    group$width <- ggplot_curve_group_style(parts, rows, "linewidth", function(part) part$width, shared)
    group$style <- ggplot_curve_group_style(parts, rows, "linestyle", function(part) part$style, shared)
    alpha <- ggplot_curve_group_style(parts, rows, "alpha", function(part) part$stroke_alpha / 255, shared)
    if (is.null(group$width) || is.null(group$style) || is.null(group$visible)) next
    guide_alpha <- !"alpha" %in% names(params$override.aes) &&
      "alpha" %in% names(params$decor[[target$key_name]]$data)
    group$opacity_editable <- !"alpha" %in% mapping && !is.null(alpha) &&
      (length(shared_parent) > 0L || guide_alpha)
    if (group$opacity_editable) group$authored_opacity <- alpha
    context <- target$context
    if (faceted) {
      group$parts <- parts[present]
      group$facet <- NULL
      context <- lapply(group$parts, `[[`, "context")
    }
    group$context <- rlang::hash(list(context, key$.value[[index]], group$group$colour))
    result[[paste0(parent_gid, ".group.", index)]] <- group
  }
  result
}

ggplot_series_edit_order <- function(gids, targets) {
  scope <- vapply(gids, function(gid) {
    group <- targets[[gid]]$group
    if (is.null(group)) 0L else if (is.null(group$shared)) 1L else 2L
  }, integer(1L))
  gids[order(scope)]
}

ggplot_mapped_errorbar_targets <- function(built, panels, draws, maps, index, mapping, layer) {
  if (!identical(as.list(layer$geom)$draw_key, ggplot2::draw_key_path)) return(list())
  guides <- built$plot$guides
  keys <- which(vapply(guides$params, function(params) {
    length(intersect(params$aesthetic, mapping)) > 0L
  }, logical(1L)))
  if (!length(keys) || !all(vapply(guides$guides[keys], function(guide) {
    identical(class(guide)[[1L]], "GuideLegend")
  }, logical(1L)))) return(list())
  groups <- ggplot_discrete_layer_groups(built, mapping, maps, index, "colour")
  if (is.null(groups)) return(list())
  layout <- built$layout
  result <- list()
  for (draw in draws) {
    final <- draw$grob
    line <- final$tavotto_cap_base %||% final
    rows <- draw$rows
    if (!inherits(line, "polyline") || !is.null(line$arrow) || !is.null(line$vp) ||
        is.null(line$id) || length(line$id) > 4096L || anyNA(line$id)) next
    caps <- ggplot_errorbar_caps(line, rows$flipped_aes)
    id <- as.character(draw$panel_id)
    membership <- groups$membership[[id]]
    if (is.null(caps) || ncol(caps$points) != nrow(rows) || length(membership) != nrow(rows)) next
    axes <- c("x", "y")
    along <- caps$axis
    across <- setdiff(axes, along)
    ends <- paste0(across, c("max", "min"))
    projected <- layout$coord$transform(rows[c(axes, ends)], layout$panel_params[[draw$panel_id]])
    if (!isTRUE(all.equal(as.numeric(line[[along]][caps$points[4L, ]]), projected[[along]])) ||
        !isTRUE(all.equal(as.numeric(line[[across]][caps$points[4L, ]]), projected[[ends[[1L]]]])) ||
        !isTRUE(all.equal(as.numeric(line[[across]][caps$points[5L, ]]), projected[[ends[[2L]]]]))) next
    groups_in_panel <- split(seq_len(nrow(rows)), membership)
    suffix <- if (panels$single) "" else paste0(".panel.", id)
    for (key in as.integer(names(groups_in_panel))) {
      selected <- groups_in_panel[[as.character(key)]]
      width <- unique(rep_len(line$gp$lwd, nrow(rows))[selected]) * ggplot_lwd_pt
      if (!is_positive_ggplot_number(width)) next
      colours <- rep_len(line$gp$col, nrow(rows))[selected]
      rgba <- if (is.character(colours) && !anyNA(colours)) grDevices::col2rgb(colours, alpha = TRUE)
      rgb <- if (!is.null(rgba)) unique(scales::alpha(colours, 1))
      opacity <- if (!"alpha" %in% mapping && !is.null(rgba)) unique(rgba[4L, ])
      context <- rlang::hash(list(rows, draw$facet, groups$keys[[key]], selected))
      gid <- paste0("layer.", index, suffix, ".errorbar.group.", key)
      result[[gid]] <- list(slot = draw$slot, child = line$name, index = index, interval = TRUE,
                            facet = draw$facet, caps = caps, cap_rows = selected, width = width,
                            stroke_width_editable = !any(c("linewidth", "size") %in% mapping),
                            color = if (length(rgb) == 1L) rgb,
                            alpha = if (length(opacity) == 1L) opacity,
                            cap_width = if (!is.null(final$tavotto_cap_style)) {
                              stored <- unique(final$tavotto_cap_style$width[selected])
                              if (is_positive_ggplot_number(stored)) stored
                            },
                            cap_label = groups$labels[[key]], clip = identical(layout$coord$clip, "on"),
                            category = list(value = as.character(groups$keys[[key]])),
                            context = context)
    }
  }
  result
}

ggplot_errorbar_group_props <- function(target) {
  c("capsize", "cap_thickness", if (isTRUE(target$stroke_width_editable)) "linewidth",
    if (!is.null(target$alpha)) "alpha", if (!is.null(target$color)) "color")
}

ggplot_errorbar_group_edits <- function(table, targets, edits) {
  gids <- intersect(names(edits), names(targets))
  selected <- Filter(function(target) !is.null(target$cap_rows), targets[gids])
  if (!length(selected)) return(table)
  key <- function(target) paste(target$slot, target$child, sep = "/")
  members <- split(names(selected), vapply(selected, key, character(1L)))
  for (group in members) {
    target <- targets[[group[[1L]]]]
    panel <- table$grobs[[target$slot]]
    line <- panel$children[[target$child]]
    caps <- ggplot_errorbar_caps(line, target$caps$axis == "y")
    if (is.null(caps) || ncol(caps$points) != ncol(target$caps$points)) {
      stop("The native R error bar cap groups changed.")
    }
    values <- list()
    for (gid in group) {
      patch <- edits[[gid]]
      if (!all(names(patch) %in% ggplot_errorbar_group_props(targets[[gid]]))) {
        stop("Invalid native R error bar group style.")
      }
      ggplot_line_style(targets[[gid]], patch)
      rows <- targets[[gid]]$cap_rows
      for (prop in names(patch)) {
        if (is.null(values[[prop]])) values[[prop]] <- rep(NA_real_, ncol(caps$points))
        values[[prop]][rows] <- patch[[prop]]
      }
    }
    panel$children[[target$child]] <- ggplot_edit_errorbar_group_styles(line, caps, values)
    table$grobs[[target$slot]] <- panel
  }
  table
}

ggplot_line_targets <- function(built, panels, layer_grobs, maps) {
  if (is.null(panels)) return(list())
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    interval <- kind %in% c("GeomErrorbar", "GeomErrorbarh")
    if (!interval && !kind %in% c("GeomLine", "GeomPath")) next
    mapping <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapping)) next
    key_name <- ggplot_curve_key_name(kind)
    guide_keys <- integer()
    if (interval && ggplot_layer_has_legend_key(built, layer, mapping)) {
      draws <- ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])
      targets <- c(targets, ggplot_mapped_errorbar_targets(built, panels, draws, maps, index, mapping, layer))
      next
    }
    if (!interval) {
      guide_keys <- ggplot_line_guide_keys(built, layer, key_name, mapping)
      if (is.null(guide_keys)) next
    }
    if (length(intersect(mapping, c("size", "linewidth", "linetype")))) next
    shared_guide <- !panels$single && length(guide_keys) > 0L
    groups <- if (!interval && length(guide_keys)) ggplot_discrete_layer_groups(
      built, mapping, maps, index, "colour"
    )
    parts <- list()
    paints <- list()
    drawn <- ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])
    for (draw in drawn) {
      final <- draw$grob
      line <- final$tavotto_cap_base %||% final$tavotto_curve_base %||% final
      suffix <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      if (!inherits(line, "polyline") || !is.null(line$arrow) || !is.null(line$vp) ||
          length(line$x) < 2L || length(line$x) > 4096L ||
          length(line$id.lengths) > 1L) next
      if (!is.null(line$id) && (length(line$id) != length(line$x) || anyNA(line$id))) next
      if (interval && is.null(line$id)) next
      width <- unique(line$gp$lwd)
      if (!is_positive_ggplot_number(width)) next
      rgba <- tryCatch(grDevices::col2rgb(unique(line$gp$col), alpha = TRUE), error = function(error) NULL)
      if (is.null(rgba) || !ncol(rgba)) next
      fixed_colour <- !"colour" %in% mapping && !length(guide_keys) && ncol(rgba) == 1L
      opacity_editable <- !interval && fixed_colour && !"alpha" %in% mapping
      if (any(rgba[4L, ] == 0L) && !opacity_editable) next
      lty <- unique(line$gp$lty)
      if (is.numeric(lty) && length(lty) == 1L && lty %in% 1:4) {
        lty <- unname(ggplot_line_types[[lty]])
      }
      style <- names(ggplot_line_types)[match(lty, ggplot_line_types, nomatch = 0L)]
      if (length(style) != 1L) next
      gid <- paste0("layer.", index, suffix, if (interval) ".errorbar" else ".line")
      facts <- list(kind, draw$rows, if (is.null(final$tavotto_curve_style)) line$gp else final$gp)
      if (!panels$single) facts <- c(facts, list(draw$facet))
      if (length(guide_keys)) {
        facts <- c(facts, lapply(built$plot$guides$params[guide_keys], function(params) {
          list(key = params$key, stroke = params$decor[[key_name]]$data)
        }))
      }
      context <- rlang::hash(facts)
      stroke_categories <- if (!is.null(groups)) ggplot_curve_stroke_categories(
        line, draw$rows, groups$membership[[as.character(draw$panel_id)]], built$layout, draw$panel_id
      )
      target <- list(
        slot = draw$slot, child = line$name, index = index, context = context, interval = interval,
        stroke_categories = stroke_categories,
        stroke_alpha = grDevices::col2rgb(line$gp$col, alpha = TRUE)[4L, ],
        curve_style = final$tavotto_curve_style,
        curve_shared_style = final$tavotto_curve_shared %||% final$tavotto_curve_style,
        facet = draw$facet, panel = draw$panel_id,
        caps = if (interval) ggplot_errorbar_caps(line, draw$rows$flipped_aes),
        cap_width = if (!is.null(final$tavotto_cap_style)) {
          stored <- unique(final$tavotto_cap_style$width)
          if (is_positive_ggplot_number(stored)) stored
        },
        guide_keys = guide_keys, key_name = key_name,
        clip = identical(built$layout$coord$clip, "on"),
        width = width * ggplot_lwd_pt, style = style,
        color = if (fixed_colour) grDevices::rgb(rgba[1L], rgba[2L], rgba[3L], maxColorValue = 255),
        opacity_editable = opacity_editable,
        alpha = if (!interval || (!"alpha" %in% mapping && length(unique(rgba[4L, ])) == 1L)) rgba[4L, 1L]
      )
      if (shared_guide) {
        parts <- c(parts, list(target))
        paints <- c(paints, list(ggplot_line_group_paint(line)))
      } else {
        targets[[gid]] <- target
        if (panels$single && !interval) {
          if (!is.null(final$tavotto_curve_style)) targets[[gid]] <- NULL
          targets <- c(targets, ggplot_line_group_targets(
            built, layer, list(target), list(ggplot_line_group_paint(line)), gid, FALSE, groups, mapping
          ))
        }
      }
    }
    # A shared key means one edit owns the category in every rendered panel.
    # Never register it with only a subset of the native layer's drawings.
    if (shared_guide && length(parts) && length(parts) == sum(!vapply(
      layer_grobs[[index]], inherits, logical(1L), "zeroGrob"
    ))) {
      gid <- paste0("layer.", index, ".line")
      shared <- ggplot_line_group_targets(built, layer, parts, paints, gid, TRUE, groups, mapping)
      targets <- c(targets, shared)
      for (i in seq_along(parts)) {
        part <- parts[[i]]
        part_gid <- paste0("layer.", index, ".panel.", part$panel, ".line")
        local <- ggplot_line_group_targets(built, layer, list(part), paints[i], part_gid, FALSE,
                                           groups, mapping, shared_parent = gid)
        targets <- c(targets, Filter(function(target) target$group$shared %in% names(shared), local))
      }
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

measure_ggplot_line <- function(table, target, group, drawn, frame_pt) {
  path <- c(table$name, ggplot_cell_name(table$layout[target$slot, ]),
            table$grobs[[target$slot]]$name)
  viewport <- ggplot_drawn_panel_viewport(drawn, table, target$slot, target$child)
  if (is.null(viewport)) return(NULL)
  tryCatch({
    grid::downViewport(viewport, strict = TRUE)
    line <- grid::grid.get(do.call(grid::gPath, as.list(c(path, target$child))), strict = TRUE)
    widths <- line$gp$lwd
    colours <- line$gp$col
    points <- seq_along(line$x)
    if (!is.null(group)) {
      slots <- ggplot_line_group_slots(line, group, target)
      ids <- unique(line$id)
      if (!length(slots)) stop("The native R curve group changed.")
      points <- which(line$id %in% ids[slots])
      widths <- rep_len(widths, length(ids))[slots]
      colours <- colours[slots]
    }
    if (!is.null(target$cap_rows)) {
      rows <- line$tavotto_cap_rows %||% rep(seq_len(ncol(target$caps$points)), each = 8L)
      points <- which(rows %in% target$cap_rows)
      ids <- unique(line$id)
      strokes <- match(unique(line$id[points]), ids)
      widths <- rep_len(widths, length(ids))[strokes]
      colours <- rep_len(colours, length(ids))[strokes]
    }
    width <- max(widths) * ggplot_lwd_pt
    paths <- ggplot_device_paths(line$x[points], line$y[points], line$id[points], FALSE, frame_pt)
    if (!length(paths) || !is_positive_ggplot_number(width)) stop("Invalid native R stroke geometry.")
    visible <- any(grDevices::col2rgb(colours, alpha = TRUE)[4L, ] > 0)
    geometry <- list(kind = if (target$interval || length(paths) != 1L) "multi_path" else "polyline",
                     paths = paths, fill = FALSE, stroke = visible, stroke_pt = width,
                     paint_order = match(line$name, table$grobs[[target$slot]]$childrenOrder))
    if (target$clip) geometry$clip <- ggplot_panel_clip(frame_pt)
    pad <- width / (2 * frame_pt)
    bbox <- ggplot_paths_bbox(paths, pad)
    capsize <- if (!is.null(target$caps)) {
      span <- ggplot_cap_span(line, target$caps, (target$cap_rows %||% 1L)[[1L]])
      abs(grid::convertUnit(span, "inches", axisFrom = target$caps$axis,
                            typeFrom = "dimension", valueOnly = TRUE)) * 72
    }
    list(bbox = as.list(bbox), geometry = geometry, capsize = capsize)
  }, error = function(error) NULL, finally = grid::upViewport(0))
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
    parts <- target$parts
    if (is.null(parts)) {
      result[[gid]] <- measure_ggplot_line(table, target, target$group, drawn, frame_pt)
      next
    }
    measured <- lapply(parts, function(part) measure_ggplot_line(table, part, target$group, drawn, frame_pt))
    if (any(lengths(measured) == 0L)) next
    geometries <- lapply(measured, `[[`, "geometry")
    width <- max(vapply(geometries, `[[`, numeric(1L), "stroke_pt"))
    visible <- any(vapply(geometries, `[[`, logical(1L), "stroke"))
    paths <- unlist(lapply(geometries, function(geometry) {
      lapply(geometry$paths, function(path) {
        path$clip <- geometry$clip
        path
      })
    }), recursive = FALSE)
    orders <- unique(vapply(geometries, `[[`, numeric(1L), "paint_order"))
    geometry <- list(kind = "multi_path", paths = paths, fill = FALSE, stroke = visible, stroke_pt = width)
    if (length(orders) == 1L) geometry$paint_order <- orders[[1L]]
    result[[gid]] <- list(bbox = as.list(ggplot_paths_bbox(paths, width / (2 * frame_pt))),
                          geometry = geometry)
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

# Binding a fixed aesthetic to an authored layer needs stock native drawing
# and the set of panels that actually draw that layer.
ggplot_fixed_layer_source <- function(state, target, edit, aesthetics, geometry = NULL) {
  layer <- state$built$plot$layers[[target$index]]
  kind <- class(layer$geom)[[1L]]
  kinds <- c("GeomPoint", "GeomLine", "GeomPath", "GeomCol", "GeomBar",
             "GeomErrorbar", "GeomErrorbarh", "GeomRibbon", "GeomArea")
  if (!kind %in% kinds) return(NULL)
  stock <- getExportedValue("ggplot2", kind)
  if (!identical(as.list(layer$geom)$draw_panel, as.list(stock)$draw_panel)) return(NULL)
  panels <- sum(!vapply(state$layer_grobs[[target$index]], inherits, logical(1L), "zeroGrob"))
  if (panels) list(layer = target$index, geom = kind, panels = panels, edited = edit,
                   aesthetics = aesthetics, geometry = geometry, facet = target$facet,
                   scoped_paint = length(layer$tavotto_facet_paint) > 0L)
}

ggplot_layer_has_group_edits <- function(targets, index, edits) {
  changed <- targets[intersect(names(targets), names(edits))]
  any(vapply(changed, function(target) {
    identical(target$index, index) && !is.null(target$group)
  }, logical(1L)))
}

ggplot_line_source_plan <- function(state, target, patch, edits) {
  if (!length(patch)) return(NULL)
  if (!is.null(target$group)) {
    value <- target$group$source_value
    if (is.null(value) || is.na(value) || length(edits[[target$group$parent]])) return(NULL)
    ggplot_line_style(target, patch)
    geometry <- list(kind = "curves", facet = target$facet, values = patch, category = list(value = value))
    return(ggplot_fixed_layer_source(state, target, patch, NULL, geometry))
  }
  if (ggplot_layer_has_group_edits(state$line_targets, target$index, edits)) return(NULL)
  style <- ggplot_line_style(target, patch)
  aesthetics <- patch[intersect("alpha", names(patch))]
  if (!is.null(style$lwd)) aesthetics$linewidth <- style$lwd / ggplot2::.pt
  if (!is.null(style$lty)) aesthetics$linetype <- style$lty
  if (!is.null(style$col)) aesthetics$colour <- style$col
  ggplot_fixed_layer_source(state, target, patch, aesthetics)
}

ggplot_errorbar_source_plan <- function(state, target, patch) {
  if (!length(patch)) return(NULL)
  if (!is.null(target$cap_rows)) {
    if (!all(names(patch) %in% ggplot_errorbar_group_props(target))) return(NULL)
    ggplot_line_style(target, patch)
    geometry <- list(kind = "caps", facet = target$facet, values = patch, category = target$category)
    return(ggplot_fixed_layer_source(state, target, patch, NULL, geometry))
  }
  style <- ggplot_line_style(target, patch)
  aesthetics <- patch[intersect("alpha", names(patch))]
  if (!is.null(style$lwd)) aesthetics$linewidth <- style$lwd / ggplot2::.pt
  if (!is.null(style$col)) aesthetics$colour <- style$col
  caps <- patch[intersect(names(patch), c("capsize", "cap_thickness"))]
  geometry <- if (length(caps)) list(kind = "caps", facet = target$facet, values = caps)
  ggplot_fixed_layer_source(state, target, patch, aesthetics, geometry)
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
      change$col <- if (is.null(target$cap_rows)) scales::alpha(value, target$alpha / 255) else value
    } else if ((target$interval || isTRUE(target$opacity_editable)) && !is.null(target$alpha) &&
               prop == "alpha" && is.numeric(value) && length(value) == 1L &&
               is.finite(value) && value >= 0 && value <= 1) {
      next
    } else if (isTRUE(target$visibility_editable) && prop == "visible" &&
               is.logical(value) && length(value) == 1L && !is.na(value)) {
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

ggplot_edit_line_part <- function(table, target, group, patch, change) {
  panel <- table$grobs[[target$slot]]
  line <- panel$children[[target$child]]
  authored <- line$tavotto_cap_style
  line <- line$tavotto_cap_base %||% line
  if (is.null(group)) {
    line$gp[names(change)] <- change
  } else {
    slots <- ggplot_line_group_slots(line, group, target)
    if (!length(slots)) stop("The native R curve group changed.")
    line <- ggplot_curve_style_strokes(line, slots, patch, authored = TRUE, local = !is.null(group$shared))
  }
  opacity <- if (identical(patch$visible, FALSE)) 0 else patch$alpha
  if (!is.null(opacity) && is.null(group)) {
    line$gp$col <- scales::alpha(line$gp$col, opacity)
  }
  if (!is.null(authored) || !is.null(patch$capsize) || !is.null(patch$cap_thickness)) {
    caps <- ggplot_errorbar_caps(line, target$caps$axis == "y")
    if (is.null(caps)) stop("The native R error bar caps changed.")
    length <- patch$capsize %||% authored$length
    width <- patch$cap_thickness %||% authored$width
    line <- ggplot_edit_errorbar_caps(line, caps, length, width)
  }
  panel$children[[target$child]] <- line
  table$grobs[[target$slot]] <- panel
  table
}

ggplot_edit_line <- function(table, target, patch) {
  change <- ggplot_line_style(target, patch)
  for (part in target$parts %||% list(target)) {
    table <- ggplot_edit_line_part(table, part, target$group, patch, change)
  }
  table
}

ggplot_authored_series_aesthetics <- function(plot) {
  styles <- lapply(plot$layers, function(layer) {
    layer$tavotto_series_aesthetics %||% if (isTRUE(layer$tavotto_curve_source)) "colour"
  })
  stats::setNames(styles, as.character(seq_along(styles))) |> Filter(f = Negate(is.null))
}

ggplot_capture_series_categories <- function(built, capture, styles) {
  lapply(names(styles), function(index) {
    i <- as.integer(index)
    ggplot_observed_series_categories(capture, i, built$data[[i]], styles[[index]])
  }) |> stats::setNames(names(styles))
}

capture_ggplot <- function(plot, frame_pt, palettes = NULL) {
  if (!inherits(plot, "ggplot")) stop("The selected object is not a ggplot.")
  facet_source <- plot$facet$tavotto_facet_adjustments
  if (length(facet_source)) plot$facet <- plot$facet$tavotto_facet_base
  legacy_ticks <- plot$facet$tavotto_tick_recipes
  if (length(legacy_ticks)) plot$facet <- plot$facet$tavotto_tick_base
  authored_ticks <- c(legacy_ticks, facet_source$tick_labels$recipes)
  for (kind in setdiff(names(facet_source), c("tick_labels", "locators"))) {
    adjustment <- facet_source[[kind]]
    plot <- ggplot_facet_adjust(plot, kind, adjustment$recipes, adjustment$install)
  }
  locator_source <- ggplot_tick_locator_baseline(plot)
  plot <- locator_source$plot
  minor_source <- plot$layout$tavotto_minor_recipes
  if (length(minor_source)) plot$layout <- ggplot_clone(plot$layout, tavotto_skip_minor_locators = TRUE)
  measured <- with_svg_frame(frame_pt, function(capture) {
    colour_capture <- new.env(parent = emptyenv())
    authored_series <- ggplot_authored_series_aesthetics(plot)
    if (!is.null(palettes) || length(authored_series)) {
      plot <- ggplot_observe_colour_maps(plot, colour_capture)
    }
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
    panels <- ggplot_cartesian_panels(table, built$layout)
    axes_target <- ggplot_grid_target(built, table, ggplot_axes_target(built, table))
    axes_target <- ggplot_frame_target(built, table, axes_target)
    axes_target <- ggplot_background_target(built, table, axes_target)
    axes_target <- ggplot_aspect_target(built, table, axes_target)
    axes_target <- ggplot_tick_side_target(built, table, axes_target)
    facet_axis_groups <- if (!is.null(panels) && !panels$single) ggplot_facet_axis_groups(built, panels$cells)
    axes_targets <- if (is.null(axes_target)) {
      ggplot_faceted_axes_targets(built, table, panels, facet_axis_groups)
    } else stats::setNames(list(axes_target), ggplot_axes_gid)
    colourbar_titles <- ggplot_colourbar_titles(built)
    text_targets <- ggplot_text_targets(table, frame_pt, colourbar_titles, axes_targets, built$layout$coord)
    tick_targets <- if (all(lengths(captured_layers$grobs) > 0L)) {
      ggplot_tick_targets(built, text_targets, colourbar_titles, axes_targets, panels)
    }
    for (gid in names(Filter(function(text) text$role == "ticks", text_targets))) {
      if (is.null(tick_targets[[gid]]$size)) {
        text_targets[[gid]]$face <- NULL
        text_targets[[gid]]$font_families <- NULL
      }
    }
    member_scopes <- ggplot_tick_member_scopes(built, tick_targets, text_targets, panels)
    tick_members <- ggplot_measure_tick_members(built$layout, text_targets, member_scopes, frame_pt)
    colour_scales <- list()
    if (all(lengths(captured_layers$grobs) > 0L)) {
      colour_scales <- ggplot_colour_scale_targets(
        built, table, text_targets, colourbar_titles, colour_capture$maps
      )
      bounds <- measure_ggplot_colour_scales(table, colour_scales, frame_pt)
      colour_scales <- colour_scales[names(bounds)]
      for (gid in names(colour_scales)) colour_scales[[gid]]$bbox <- bounds[[gid]]
    }
    line_targets <- ggplot_line_targets(built, panels, captured_layers$grobs, colour_capture$maps)
    line_geometry <- measure_ggplot_lines(table, line_targets, frame_pt)
    line_targets <- line_targets[names(line_geometry)]
    axes_bbox <- measure_ggplot_axes(table, axes_targets, frame_pt)
    has_sized_title <- any(vapply(text_targets, function(text) !is.null(text$title_dimension), logical(1L)))
    point_targets <- ggplot_point_targets(built, panels, captured_layers$grobs, colour_capture$maps)
    rebuild_guides <- has_sized_title || length(axes_targets) || length(tick_targets) || length(colour_scales) ||
      any(vapply(line_targets, function(line) length(line$guide_keys) > 0L, logical(1L))) ||
      any(vapply(point_targets, function(points) !is.null(points$group), logical(1L)))
    point_boxes <- measure_ggplot_points(table, point_targets, frame_pt)
    point_targets <- point_targets[names(point_boxes)]
    for (gid in names(point_targets)) point_targets[[gid]]$bbox <- point_boxes[[gid]]
    bar_targets <- ggplot_bar_targets(built, panels, captured_layers$grobs, colour_capture$maps)
    bar_geometry <- measure_ggplot_bars(table, bar_targets, frame_pt)
    bar_targets <- bar_targets[names(bar_geometry)]
    ribbon_targets <- ggplot_ribbon_targets(built, panels, captured_layers$grobs, colour_capture$maps)
    ribbon_geometry <- measure_ggplot_ribbons(table, ribbon_targets, frame_pt)
    ribbon_targets <- ribbon_targets[names(ribbon_geometry)]
    layer_priorities <- ggplot_layer_priorities(panels, captured_layers$grobs)
    layer_order_targets <- ggplot_layer_order_targets(
      table, layer_priorities, c(line_targets, point_targets, bar_targets, ribbon_targets)
    )
    legend_targets <- ggplot_legend_boxes(table)
    legend_boxes <- measure_ggplot_legends(legend_targets, frame_pt)
    legend_targets <- legend_targets[!vapply(legend_boxes, is.null, logical(1L))]
    for (gid in names(legend_targets)) legend_targets[[gid]]$bbox <- legend_boxes[[gid]]
    list(
      table = table,
      text_targets = text_targets,
      tick_targets = tick_targets,
      tick_member_scopes = member_scopes,
      tick_members = tick_members,
      authored_tick_recipes = authored_ticks,
      authored_locators = ggplot_captured_locators(tick_targets, built$layout$layout,
                                                  locator_source$locators,
                                                  c(facet_source$locators$recipes, minor_source)),
      colour_scales = colour_scales,
      palettes = palettes,
      built = if (rebuild_guides) built,
      layer_grobs = if (rebuild_guides) captured_layers$grobs,
      layer_priorities = layer_priorities,
      layer_order_targets = layer_order_targets,
      source_layer_order = ggplot_stock_layer_order_facet(built$layout$facet),
      authored_layer_order = ggplot_authored_layer_order(table, panels, facet_source$layer_order$recipes),
      axes_targets = axes_targets,
      facet_axis_groups = facet_axis_groups,
      axes_bbox = axes_bbox,
      line_targets = line_targets,
      line_geometry = line_geometry,
      series_categories = ggplot_capture_series_categories(built, colour_capture, authored_series),
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
  observed <- if (length(state$authored_locators)) new.env(parent = emptyenv())
  svg <- tryCatch(render_ggplot(state, observed = observed), tavotto_legend_svg = function(error) NULL)
  if (is.null(svg)) {
    state$legend_targets <- list()
    svg <- render_ggplot(state, observed = observed)
  }
  if (!is.null(observed)) state$tick_member_scopes <- ggplot_anchor_authored_ticks(state, observed$layout)
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
ggplot_element_faces <- c("plain", "bold", "italic", "bold.italic")
ggplot_font_families <- c("sans-serif" = "sans", serif = "serif", monospace = "mono")

ggplot_installed_text_fonts <- function() {
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

ggplot_text_family <- function(target) {
  if (!length(target$font_families)) return(NULL)
  family <- target$paint$fontfamily
  if (is.null(family) || identical(family, "")) return("sans-serif")
  if (!is.character(family) || length(family) != 1L || is.na(family)) return(NULL)
  index <- match(family, ggplot_font_families)
  if (is.na(index)) family else names(ggplot_font_families)[[index]]
}

ggplot_text_cex <- function(paint) {
  if (is.null(paint$cex)) 1 else paint$cex
}

ggplot_text_edit <- function(edit, tick) {
  guide_props <- c(ggplot_tick_props,
                   if (identical(tick$kind, "axis")) c(ggplot_axis_mark_props, "direction", "visible"),
                   if (!is.null(tick$size)) c("fontsize", "fontfamily"),
                   if (!is.null(tick$rotation)) "rotation")
  edit[setdiff(names(edit), guide_props)]
}

ggplot_text_paint <- function(target, edits) {
  paint <- target$paint
  face <- target$face
  family <- ggplot_text_family(target)
  for (prop in names(edits)) {
    value <- edits[[prop]]
    if (prop == "text" && !is.null(target$text_slot)) next
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
               value %in% c(family, names(target$font_families))) {
      if (value != family) paint$fontfamily <- target$font_families[[value]]
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

ggplot_axis_mark_theme <- function(gid, marks, edit) {
  changes <- list()
  for (prop in intersect(ggplot_axis_mark_props, names(edit))) {
    minor <- startsWith(prop, "minor_")
    style <- sub("^minor_", "", prop)
    mark <- if (minor && isTRUE(marks$minor_editable)) marks$minor else if (!minor) marks$major
    limits <- ggplot_mark_fields[[style]]
    value <- edit[[prop]]
    if (is.null(mark) || !is.numeric(value) || length(value) != 1L || !is.finite(value) ||
        value < limits[["min"]] || value > limits[["max"]]) {
      stop("The requested native R tick mark style is unavailable.")
    }
    changes[[prop]] <- value
  }
  direction <- edit$direction
  if (!is.null(direction) &&
      (is.null(marks$direction) || !is.character(direction) || length(direction) != 1L ||
       !direction %in% ggplot_axis_tick_directions)) {
    stop("The requested native R tick direction is unavailable.")
  }
  if (!length(changes) && is.null(direction)) return(list())
  suffix <- substring(gid, 6L, nchar(gid) - 6L)
  elements <- list()
  for (style in names(ggplot_mark_fields)) {
    major <- changes[[style]]
    minor <- changes[[paste0("minor_", style)]]
    for (kind in c("major", "minor")) {
      mark <- marks[[kind]]
      if (is.null(mark)) next
      # The minor mark can inherit both its linewidth and a relative length
      # from the major mark. Freeze the original resolved value independently.
      value <- if (kind == "major") major else if (!is.null(minor)) minor else if (!is.null(major)) mark[[style]]
      if (style == "length" && !is.null(direction) && is.null(value)) value <- mark$length
      if (is.null(value)) next
      name <- paste0(if (kind == "major") "axis.ticks" else "axis.minor.ticks",
                     if (style == "length") ".length", ".", suffix)
      if (style == "length") {
        multiplier <- if (identical(direction, "inout")) 0.5 else if (
          identical(direction, "in") || (is.null(direction) && mark$size < 0)
        ) -1 else 1
        elements[[name]] <- grid::unit(value * multiplier, "bigpts")
      } else {
        line <- mark$line
        line$linewidth <- value / (ggplot2::.pt * ggplot_lwd_pt)
        elements[[name]] <- line
      }
    }
  }
  elements
}

ggplot_centered_axis_guide <- function() {
  ggplot2::ggproto(NULL, ggplot2::GuideAxis,
    build_ticks = function(key, elements, params, position = params$opposite) {
      mark <- ggplot2::GuideAxis$build_ticks(key, elements, params, position)
      across <- if (position %in% c("top", "bottom")) "y" else "x"
      center <- function(stroke) {
        if (inherits(stroke, "zeroGrob")) return(stroke)
        values <- stroke[[across]]
        count <- length(stroke$id.lengths)
        if (!inherits(stroke, "polyline") || !count || length(values) != count * 2L ||
            any(stroke$id.lengths != 2L)) {
          stop("The native R axis did not draw the expected tick strokes.")
        }
        indices <- seq_len(count) * 2L
        values[indices] <- 2 * values[indices] - values[indices - 1L]
        stroke[[across]] <- values
        stroke
      }
      if (inherits(mark, "gTree")) {
        mark$children <- do.call(grid::gList, lapply(mark$children, center))
      } else mark <- center(mark)
      mark
    }
  )
}

ggplot_axis_style_edits <- function(state, edits) {
  result <- list()
  axis_ticks <- Filter(function(tick) identical(tick$kind, "axis"), state$tick_targets)
  for (gid in intersect(names(edits), names(axis_ticks))) {
    edit <- edits[[gid]]
    face_props <- intersect(names(ggplot_face_options), names(edit))
    font_props <- c(face_props, intersect("fontfamily", names(edit)))
    size <- edit$fontsize
    rotation <- edit$rotation
    visible <- edit$visible
    target <- state$text_targets[[gid]]
    tick <- state$tick_targets[[gid]]
    marks <- ggplot_axis_mark_theme(target$axis_base, tick$marks, edit)
    if (is.null(size) && !length(font_props) && is.null(rotation) && !length(marks) &&
        is.null(visible)) next
    if (!is.null(visible) &&
        (!identical(tick$kind, "axis") || !is.logical(visible) || length(visible) != 1L || is.na(visible))) {
      stop("The requested native R tick label visibility is unavailable.")
    }
    if ((length(font_props) || !is.null(size)) && is.null(tick$size)) {
      stop("The requested native R tick typography is unavailable.")
    }
    if (!is.null(rotation) &&
        (is.null(tick$rotation) || !is.numeric(rotation) || length(rotation) != 1L ||
         !is.finite(rotation) || abs(rotation) > 90)) {
      stop("The native R tick rotation must be between -90 and 90 degrees on a theme-managed guide.")
    }
    if (!is.null(size) && (!is_positive_ggplot_number(size) || size < 3 || size > 200)) {
      stop("The native R tick font size must be between 3 and 200 points.")
    }
    styled <- if (length(font_props)) ggplot_text_paint(target, edit[font_props])
    face <- if (length(face_props)) ggplot_element_faces[[styled$paint$font]]
    family <- if (!is.null(edit$fontfamily)) styled$paint$fontfamily
    result[[gid]] <- list(size = size, face = face, rotation = rotation, family = family,
                          marks = marks, direction = edit$direction, visible = visible)
  }
  result
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
      face <- ggplot_element_faces[[styled$paint$font]]
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

ggplot_guide_stroke_edits <- function(state, edits) {
  gids <- intersect(names(edits), names(state$line_targets))
  edits[gids[vapply(state$line_targets[gids], function(line) {
    length(line$guide_keys) > 0L && is.null(line$group$shared)
  }, logical(1L))]]
}

ggplot_guide_point_edits <- function(state, edits) {
  gids <- intersect(names(edits), names(state$point_targets))
  edits[gids[vapply(state$point_targets[gids], function(target) {
    !is.null(target$group) && is.null(target$group$shared)
  }, logical(1L))]]
}

ggplot_table_for <- function(state, edits) {
  layer_order <- ggplot_layer_order_edits(state, edits)
  inspect_slots <- union(names(state$authored_layer_order), vapply(
    state$layer_order_targets[names(layer_order)], function(target) as.character(target$slot), character(1L)
  ))
  layer_priorities <- state$layer_priorities
  for (gid in names(layer_order)) edits[[gid]]$zorder <- NULL
  edits <- Filter(length, edits)
  edits <- ggplot_authored_locator_edits(state, edits)
  edits <- ggplot_expand_point_groups(state, edits)
  table <- state$table
  layout <- state$built$layout
  text_targets <- state$text_targets
  legend_targets <- state$legend_targets
  stops <- NULL
  axes <- NULL
  tick_values <- NULL
  label_originals <- NULL
  axes_target <- state$axes_targets[[ggplot_axes_gid]]
  axes_edits <- edits[[ggplot_axes_gid]]
  aspect <- axes_edits[["aspect"]]
  grid_edits <- axes_edits[intersect(names(axes_edits), ggplot_grid_keys)]
  frame_edits <- axes_edits[intersect(names(axes_edits), ggplot_frame_keys)]
  side_edits <- axes_edits[intersect(names(axes_edits), ggplot_tick_side_keys)]
  if (length(side_edits) && !all(names(side_edits) %in% names(axes_target$tick_sides))) {
    stop("The requested native R tick side is unavailable.")
  }
  if (length(frame_edits)) ggplot_validate_frame_edits(axes_target$frame, frame_edits)
  axis_changes <- ggplot_axis_edits(
    axes_target, axes_edits[setdiff(names(axes_edits), c(names(grid_edits), names(frame_edits),
                                                            names(side_edits), "facecolor", "aspect"))])
  facet_changes <- ggplot_facet_axis_edits(state$axes_targets, edits)
  layer_targets <- state[c("line_targets", "point_targets", "bar_targets", "ribbon_targets")]
  tick_edits <- Filter(length, lapply(edits, function(edit) edit[intersect(names(edit), ggplot_tick_props)]))
  axis_styles <- ggplot_axis_style_edits(state, edits)
  member_edits <- ggplot_tick_member_edits(state$tick_member_scopes, edits)
  colour_edits <- edits[intersect(names(edits), names(state$colour_scales))]
  guide_strokes <- ggplot_guide_stroke_edits(state, edits)
  point_edits <- ggplot_guide_point_edits(state, edits)
  titles <- list()
  for (gid in intersect(names(edits), names(ggplot_text_slots))) {
    value <- edits[[gid]]$text
    if (is.null(value)) next
    slot <- text_targets[[gid]]$text_slot
    if (is.null(slot) || !is.character(value) || length(value) != 1L || is.na(value)) {
      stop("The requested native R title text is unavailable.")
    }
    titles[[slot]] <- value
  }
  title_edits <- ggplot_title_theme(text_targets, edits)
  theme_edits <- stats::setNames(title_edits, unname(ggplot_title_theme_names[names(title_edits)]))
  if (!is.null(axes_edits$facecolor)) {
    theme_edits$panel.background <- ggplot_background_style(axes_target$background, axes_edits$facecolor)
  }
  if (length(tick_edits) || length(colour_edits) || length(axis_styles) || length(guide_strokes) ||
      length(point_edits) || length(axis_changes) || length(facet_changes) || length(grid_edits) || length(theme_edits) ||
      !is.null(aspect) || length(side_edits) || length(member_edits) || length(titles) || length(state$authored_tick_recipes)) {
    # A source writeback can move typography into the plot theme while
    # coordinate and guide edits stay in the document. Both scenes must
    # rebuild their guides on the same metrics device as the initial capture.
    guides <- with_svg_frame(state$frame_pt, function(capture) {
      ggplot_guide_table(
        state, tick_edits, colour_edits, axis_styles, guide_strokes, axis_changes,
        grid_edits, theme_edits, aspect, side_edits, facet_changes, point_edits, member_edits,
        titles, capture_order = length(inspect_slots) > 0L
      )
    })$value
    table <- guides$table
    layout <- guides$layout
    stops <- guides$stops
    axes <- guides$axes
    tick_values <- guides$tick_values
    label_originals <- guides$label_originals
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
      current <- ggplot_text_target(table, target$slots, target$role, target$title_child, group,
                                    allow_empty = !is.null(target$text_slot) || gid %in% guides$member_groups)
      changes_ticks <- !is.null(tick_edits[[gid]]) || gid %in% c(guides$guide_ticks, guides$changed_ticks) ||
        (target$role == "ticks" && substr(gid, 6L, 6L) %in% names(axis_changes))
      label <- (if (!is.null(target$text_slot)) titles[[target$text_slot]]) %||%
        guides$title_changes[[gid]] %||% target$label
      if (is.null(current) || !identical(label, current$label) ||
          (!changes_ticks && !identical(target$tick_labels, current$tick_labels))) {
        stop("The native R text layout changed while formatting.")
      }
      typography <- axis_styles[[gid]]
      size <- typography$size
      if (!is.null(size) && !isTRUE(all.equal(size, current$paint$fontsize * ggplot_text_cex(current$paint)))) {
        stop("The native R axis guide did not apply the requested font size.")
      }
      if (!is.null(typography$face) &&
          !identical(ggplot_element_faces[[current$paint$font]], typography$face)) {
        stop("The native R axis guide did not apply the requested font face.")
      }
      if (!is.null(typography$rotation) && !isTRUE(all.equal(current$rotation, typography$rotation))) {
        stop("The native R axis guide did not apply the requested label rotation.")
      }
      if (!is.null(typography$family) && !identical(current$paint$fontfamily, typography$family)) {
        stop("The native R axis guide did not apply the requested font family.")
      }
      if (is.null(target$face)) current$face <- NULL
      if (!is.null(target$text_slot)) current$hidden <- !nzchar(label)
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
    if (length(axis_changes) || length(facet_changes)) {
      layer_targets <- lapply(layer_targets, function(targets) {
        ggplot_rebind_layer_targets(table, targets, state$table, state$layer_grobs, guides$layer_grobs)
      })
    }
    if (length(inspect_slots)) {
      layer_priorities <- ggplot_layer_priorities(ggplot_cartesian_panels(table, layout), guides$layer_grobs)
    }
  }
  member_ids <- unlist(lapply(member_edits, names), use.names = FALSE)
  ordered <- ggplot_series_edit_order(setdiff(names(edits), member_ids),
                                      c(layer_targets$line_targets, layer_targets$bar_targets))
  for (gid in ordered) {
    if (gid == ggplot_axes_gid || !is.null(colour_edits[[gid]]) || gid %in% names(title_edits)) next
    facet <- state$axes_targets[[gid]]
    if (!is.null(facet)) {
      decorations <- edits[[gid]][setdiff(names(edits[[gid]]), ggplot_axis_props)]
      if (length(decorations)) {
        table <- ggplot_edit_facet_axes(table, layout, state$built$plot$theme, facet, decorations)
      }
      next
    }
    line <- layer_targets$line_targets[[gid]]
    if (!is.null(line)) {
      if (is.null(line$cap_rows)) table <- ggplot_edit_line(table, line, edits[[gid]])
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
      table$grobs[[legend$slot]] <- ggplot_move_legend_box(
        table$grobs[[legend$slot]], ggplot_legend_position(edits[[gid]]))
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
  table <- ggplot_errorbar_group_edits(table, layer_targets$line_targets, edits)
  if (length(frame_edits)) {
    table <- ggplot_frame_edit(table, axes_target$slot, axes_target$frame$fallback, frame_edits)
  }
  if (length(layer_order)) {
    table <- ggplot_order_panel_layers(table, layer_priorities, state$layer_order_targets, layer_order,
                                      state$authored_layer_order)
  }
  c(list(table = table, layout = layout, text = text_targets, legends = legend_targets,
         stops = stops, axes = axes,
         layer_paint = ggplot_layer_paint_orders(table, layer_priorities, inspect_slots),
         tick_values = tick_values, label_originals = label_originals),
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

ggplot_data_fingerprint <- function(state) {
  rlang::hash(state$layers)
}

render_ggplot <- function(state, edits = list(), observed = NULL) {
  marked <- new.env(parent = emptyenv())
  svg <- with_svg_frame(state$frame_pt, function(capture) {
    grid::grid.newpage()
    scene <- ggplot_table_for(state, edits)
    if (!is.null(observed)) observed$layout <- scene$layout
    table <- scene$table
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
  previous <- grDevices::dev.cur()
  device <- open_ggplot_pdf(state$frame_pt, path)
  on.exit(close_ggplot_device(device, previous))
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
  on.exit(close_ggplot_device(device, before))
  grid::grid.newpage()
  scene <- ggplot_table_for(state, edits)
  grid::grid.draw(scene$table)
  if (with_manifest) {
    # Measure the actual raster canvas, including any pixel rounding.
    frame_pt <- grDevices::dev.size("in") * 72
    return(list(text = measure_ggplot_targets(scene$text, frame_pt),
                tick_members = ggplot_measure_tick_members(
                  scene$layout, scene$text, state$tick_member_scopes, frame_pt, scene$label_originals),
                legends = measure_ggplot_legends(scene$legends, frame_pt),
                axes = scene$axes,
                layer_paint = scene$layer_paint,
                axes_bbox = measure_ggplot_axes(scene$table, state$axes_targets, frame_pt),
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
  requested <- edits
  layer_order <- ggplot_layer_order_edits(state, edits)
  for (gid in names(layer_order)) edits[[gid]]$zorder <- NULL
  edits <- Filter(length, edits)
  edits <- ggplot_authored_locator_edits(state, edits)
  edits <- ggplot_expand_point_groups(state, edits)
  # A resized title can move the entire plot. Re-measure even targets whose
  # own paint was left alone so the hit regions follow the rendered layout.
  legend_gids <- names(state$legend_targets)
  facet_changes <- ggplot_facet_axis_edits(state$axes_targets, edits)
  reflow <- length(state$authored_tick_recipes) > 0L ||
    length(ggplot_tick_member_edits(state$tick_member_scopes, edits)) > 0L ||
    any(vapply(edits, function(edit) any(c("text", "fontsize", "fontfamily", ggplot_tick_props,
                                                ggplot_axis_mark_props, "direction", "visible") %in% names(edit)),
                      logical(1L))) ||
    any(vapply(names(edits), function(gid) {
      identical(state$tick_targets[[gid]]$kind, "axis") &&
        any(names(edits[[gid]]) %in% c("rotation", names(ggplot_face_options)))
    }, logical(1L))) ||
    any(names(edits) %in% c(ggplot_axes_gid, legend_gids, names(state$colour_scales))) ||
    length(facet_changes) > 0L || length(ggplot_guide_stroke_edits(state, edits)) > 0L ||
    any(vapply(ggplot_guide_point_edits(state, edits), function(edit) {
      any(c("size", "linewidth") %in% names(edit))
    }, logical(1L)))
  line_edits <- length(layer_order) > 0L || any(names(edits) %in% names(state$line_targets))
  point_edits <- any(names(edits) %in% names(state$point_targets))
  bar_edits <- length(layer_order) > 0L || any(names(edits) %in% names(state$bar_targets))
  ribbon_edits <- length(layer_order) > 0L || any(names(edits) %in% names(state$ribbon_targets))
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
      scene <- ggplot_table_for(state, requested)
      grid::grid.draw(scene$table)
      list(text = measure_ggplot_targets(scene$text[geometry_gids], state$frame_pt),
           tick_members = if (reflow) ggplot_measure_tick_members(
             scene$layout, scene$text, state$tick_member_scopes, state$frame_pt, scene$label_originals),
           legends = if (reflow) measure_ggplot_legends(scene$legends, state$frame_pt),
           axes = scene$axes,
           layer_paint = scene$layer_paint,
           axes_bbox = if (reflow) measure_ggplot_axes(scene$table, state$axes_targets, state$frame_pt),
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
  facet_ticks <- ggplot_facet_tick_edits(state, edits)
  requested_facets <- ggplot_facet_tick_edits(state, requested)
  colour_anchors <- ggplot_colour_source_anchors(state$colour_scales)
  elements <- lapply(names(state$text_targets), function(gid) {
    target <- state$text_targets[[gid]]
    edit <- edits[[gid]]
    tick <- state$tick_targets[[gid]]
    authored <- state$authored_locators[[tick$locator_scope %||% ""]]
    input <- requested[[gid]]
    if (!is.null(tick$scope)) {
      edit <- utils::modifyList(edit %||% list(), facet_ticks[[tick$scope]] %||% list())
      input <- utils::modifyList(input %||% list(), requested_facets[[tick$scope]] %||% list())
    }
    patch <- ggplot_text_edit(edit, tick)
    guide_color <- if (endsWith(gid, ".colourbar.ticks")) {
      edits[[sub("\\.ticks$", "", gid)]]$tick_color
    }
    color <- patch$color %||% guide_color %||% target$color
    painted <- ggplot_text_paint(target, patch)
    face <- painted$face
    bbox <- if (is.null(measured$text[[gid]])) target$bbox else measured$text[[gid]]$bbox
    color_field <- list(prop = "color", type = "color", value = color)
    if (!is.null(guide_color)) color_field$value_original <- target$color
    editable <- list(color_field)
    if (identical(tick$kind, "axis")) {
      field <- list(prop = "visible", type = "bool", value = edit$visible %||% TRUE)
      if (!is.null(edit$visible)) field$value_original <- TRUE
      editable[[length(editable) + 1L]] <- field
    }
    if (!is.null(tick$marks$direction)) {
      field <- list(prop = "direction", type = "enum", options = as.list(ggplot_axis_tick_directions),
                    strict_options = TRUE, value = edit$direction %||% tick$marks$direction)
      if (!is.null(edit$direction)) field$value_original <- tick$marks$direction
      editable[[length(editable) + 1L]] <- field
    }
    for (kind in c("major", "minor")) {
      mark <- tick$marks[[kind]]
      if (is.null(mark) || (kind == "minor" && !isTRUE(tick$marks$minor_editable))) next
      for (style in names(ggplot_mark_fields)) {
        prop <- if (kind == "major") style else paste0("minor_", style)
        field <- c(list(prop = prop, type = "number", unit = "pt"),
                   as.list(ggplot_mark_fields[[style]]))
        field$value <- if (is.null(edit[[prop]])) round(mark[[style]], 2) else edit[[prop]]
        if (!is.null(edit[[prop]])) field$value_original <- round(mark[[style]], 2)
        editable[[length(editable) + 1L]] <- field
      }
    }
    if (!is.null(tick$values)) {
      field <- list(prop = "format", type = "enum", value = if (is.null(edit$format)) "auto" else edit$format,
                    options = as.list(ggplot_number_formats), strict_options = TRUE)
      if (!is.null(input$format)) field$value_original <- authored$format %||% "auto"
      editable[[length(editable) + 1L]] <- field
      if (!is.null(tick$spacing)) {
        mode <- ggplot_major_tick_mode(edit)
        default_step <- tick$spacing$step
        mode_field <- list(prop = "major_mode", type = "enum", value = mode,
                           options = as.list(ggplot_major_tick_modes(tick$spacing)),
                           strict_options = TRUE)
        if (!is.null(input$major_mode)) mode_field$value_original <- ggplot_major_tick_mode(authored)
        editable[[length(editable) + 1L]] <- mode_field
        if (!is.null(default_step)) {
          step_field <- list(prop = "major_step", type = "number",
                             value = if (is.null(edit$major_step)) default_step else edit$major_step,
                             min = 0, step = default_step / 10)
          if (!is.null(input$major_step)) step_field$value_original <- authored$major_step %||% default_step
          editable[[length(editable) + 1L]] <- step_field
        }
        values <- measured$tick_values[[gid]]
        if (is.null(values)) values <- tick$values
        if (mode == "fixed" && length(edit$major_values)) values <- ggplot_fixed_tick_values(edit$major_values)
        fixed_field <- list(prop = "major_values", type = "number_list", value = as.list(values))
        if (!is.null(input$major_values)) {
          fixed_field$value_original <- as.list(authored$major_values %||% tick$values)
        }
        editable[[length(editable) + 1L]] <- fixed_field
        editable <- c(editable, ggplot_minor_tick_fields(tick$spacing$minor, edit))
      }
    }
    if (!is.null(tick$rotation)) {
      field <- list(prop = "rotation", type = "number", value = edit$rotation %||% tick$rotation,
                    min = -90, max = 90, step = 5, unit = "\u00b0")
      if (!is.null(edit$rotation)) field$value_original <- tick$rotation
      editable[[length(editable) + 1L]] <- field
    }
    for (prop in names(face)) {
      field <- list(prop = prop, type = "enum", value = face[[prop]],
                    options = as.list(ggplot_face_options[[prop]]), strict_options = TRUE)
      if (!is.null(patch[[prop]])) field$value_original <- target$face[[prop]]
      editable[[length(editable) + 1L]] <- field
    }
    family_original <- ggplot_text_family(target)
    if (!is.null(family_original)) {
      requested <- edit$fontfamily
      family <- list(prop = "fontfamily", type = "enum",
                     value = if (is.null(requested)) family_original else requested,
                     options = as.list(unique(c(family_original, names(target$font_families)))),
                     strict_options = TRUE)
      if (!is.null(requested)) {
        family$value_original <- family_original
        if (requested != family_original) family$resolved_family <- target$font_families[[requested]]
      }
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
    current_label <- target$label
    if (!is.null(target$text_slot)) {
      current_label <- edit$text %||% target$label
      content <- list(prop = "text", type = "text", value = current_label)
      if (!is.null(edit$text)) content$value_original <- target$label
      editable <- c(editable, list(content))
    }
    element <- list(
      gid = gid, role = target$role, label = current_label,
      bbox = as.list(bbox), draggable = FALSE, editable = editable
    )
    locator_input <- input[intersect(names(input), ggplot_locator_props)]
    if (!is.null(tick$locator_axis) && length(locator_input)) {
      style <- edit[intersect(names(edit), ggplot_locator_props)]
      if (identical(ggplot_major_tick_mode(style), "step")) {
        style$major_step <- style$major_step %||% tick$spacing$step
      }
      element$source_tick_locator <- list(axis = tick$locator_axis, facet = tick$locator_facet, style = style)
    }
    minor_input <- input[intersect(names(input), ggplot_minor_tick_props)]
    if (!is.null(tick$locator_axis) && !is.null(tick$spacing$minor) && length(minor_input)) {
      style <- edit[intersect(names(edit), ggplot_minor_tick_props)]
      if (identical(style$minor_mode, "step")) {
        style$minor_step <- style$minor_step %||% tick$spacing$minor$step
      }
      element$source_minor_locator <- list(axis = tick$locator_axis, facet = tick$locator_facet, style = style)
    }
    if (!is.null(target$text_slot) && !is.null(edit$text)) {
      element$title_text_baseline <- target$label
      element$source_title_text <- list(slot = target$text_slot, text = current_label)
    }
    if (!is.null(target$legend_title)) element$legend_title <- target$legend_title
    if (!is.null(target$tick_labels)) element$tick_labels <- target$tick_labels
    if (identical(tick$kind, "axis") && tick$axes_gid %in% names(state$axes_targets) &&
        length(tick$axis) == 1L && tick$axis %in% ggplot_axis_names) {
      element$tick_host <- list(axes_gid = tick$axes_gid, axis = tick$axis)
      if (!is.null(tick$scope)) element$tick_context <- state$axes_targets[[tick$axes_gid]]$context
    }
    if (length(tick$links)) {
      common <- intersect(ggplot_tick_props, vapply(editable, `[[`, character(1L), "prop"))
      element$alias_pure <- as.list(common)
      element$alias_covers <- stats::setNames(lapply(common, function(prop) {
        stats::setNames(list(as.list(tick$links)), prop)
      }), common)
    }
    scope <- state$tick_member_scopes[[gid]]
    element$tick_member_prefix <- scope$prefix
    element$tick_member_context <- scope$context
    element$colour_source_anchor <- colour_anchors[[gid]]
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
    edit <- requested[[gid]]
    source <- if (length(target$source_titles) && !is.null(edit$loc_frac)) {
      list(titles = as.list(target$source_titles), position = as.list(ggplot_legend_position(edit)))
    }
    list(gid = gid, role = "legend", label = "\u56fe\u4f8b", bbox = as.list(bbox),
         draggable = TRUE, drag_prop = "loc_frac", anchor = as.list(c(bbox[[1L]], bbox[[2L]] + bbox[[4L]])),
         editable = list(), legend_context = target$context, source_legend_position = source)
  })
  local_groups <- Filter(length, lapply(state$line_targets, function(target) target$group$shared))
  group_members <- if (length(local_groups)) split(names(local_groups), unlist(local_groups, use.names = FALSE))
  lines <- lapply(names(state$line_targets), function(gid) {
    target <- state$line_targets[[gid]]
    patch <- edits[[gid]]
    geometry <- if (is.null(measured$lines)) state$line_geometry[[gid]] else measured$lines[[gid]]
    if (is.null(geometry)) stop("The native R curve could not be measured.")
    if (target$interval) {
      can_edit_width <- is.null(target$cap_rows) || isTRUE(target$stroke_width_editable)
      original <- if (can_edit_width) list(linewidth = target$width) else list()
      if (!is.null(target$color)) original <- c(list(color = target$color), original)
      if (!is.null(target$alpha)) original$alpha <- target$alpha / 255
      current <- original
      if (!is.null(target$caps)) {
        original$capsize <- state$line_geometry[[gid]]$capsize
        original$cap_thickness <- target$cap_width %||% target$width
        current$capsize <- geometry$capsize
        current$cap_thickness <- target$cap_width %||% patch$linewidth %||% target$width
      }
      current[names(patch)] <- patch
      geometry$capsize <- NULL
      label <- ggplot_layer_label("\u8bef\u5dee\u68d2", target)
      if (!is.null(target$cap_label)) label <- paste(label, target$cap_label, sep = " \u00b7 ")
      element <- c(list(gid = gid, role = "errorbar",
                    label = label, draggable = FALSE,
                    editable = list(), errorbar_series = list(
                      context = target$context, current = current,
                      original = original, edited = as.list(names(patch)))), geometry)
      element$source_layer <- ggplot_errorbar_source_plan(state, target, patch)
      return(element)
    }
    effective <- if (is.null(target$group)) patch else c(patch, edits[[target$group$parent]])
    if (!is.null(target$group$shared)) effective <- c(effective, edits[[target$group$shared]])
    width <- list(prop = "linewidth", type = "number", value = effective$linewidth %||% target$width,
                  min = 0.1, max = 20, step = 0.1, unit = "pt")
    if (!is.null(patch$linewidth)) width$value_original <- target$width
    style <- list(prop = "linestyle", type = "enum",
                  value = effective$linestyle %||% target$style,
                  options = as.list(names(ggplot_line_types)), strict_options = TRUE)
    if (!is.null(patch$linestyle)) style$value_original <- target$style
    editable <- list(width, style)
    if (!is.null(target$color)) {
      color <- list(prop = "color", type = "color",
                    value = if (is.null(patch$color)) target$color else patch$color)
      if (!is.null(patch$color)) color$value_original <- target$color
      editable <- c(editable, list(color))
    }
    if (isTRUE(target$opacity_editable)) {
      original <- target$authored_opacity %||% (target$alpha / 255)
      alpha <- list(prop = "alpha", type = "number", value = effective$alpha %||% original,
                    min = 0, max = 1, step = 0.05)
      if (!is.null(patch$alpha)) alpha$value_original <- original
      editable <- c(editable, list(alpha))
    }
    if (isTRUE(target$visibility_editable)) {
      visible <- list(prop = "visible", type = "bool", value = effective$visible %||% target$visible)
      if (!is.null(patch$visible)) visible$value_original <- target$visible
      editable <- c(editable, list(visible))
    }
    label <- ggplot_layer_label("\u66f2\u7ebf", target)
    if (length(target$parts)) label <- paste(label, "\u5168\u90e8\u5206\u9762", sep = " \u00b7 ")
    if (!is.null(target$group)) label <- paste(label, target$group$label, sep = " \u00b7 ")
    element <- c(list(gid = gid, role = "line", label = label,
                      draggable = FALSE, editable = editable, line_context = target$context), geometry)
    element$source_layer <- ggplot_line_source_plan(state, target, patch, edits)
    members <- group_members[[gid]]
    if (length(members)) {
      props <- vapply(editable, `[[`, character(1L), "prop")
      element$alias_covers <- stats::setNames(lapply(props, function(prop) {
        stats::setNames(list(as.list(members)), prop)
      }), props)
    }
    element
  })
  colour_scales <- if (length(state$colour_scales)) {
    ggplot_colour_scale_elements(state, edits, measured$colour_scales, measured$scale_stops, colour_anchors)
  }
  axes_bbox <- if (is.null(measured$axes_bbox)) state$axes_bbox else measured$axes_bbox
  axes <- lapply(names(state$axes_targets), function(gid) {
    target <- state$axes_targets[[gid]]
    ggplot_axes_element(gid, target, measured$axes[[gid]], axes_bbox[[gid]], edits[[gid]],
                        ggplot_facet_axis_changes(target, facet_changes))
  })
  elements <- c(axes, legends, elements, unname(measured$tick_members %||% state$tick_members), lines,
                ggplot_point_elements(state, edits, measured$points),
                ggplot_bar_elements(state, edits, measured$bars),
                ggplot_ribbon_elements(state, edits, measured$ribbons), colour_scales)
  paint <- measured$layer_paint %||% ggplot_layer_paint_orders(
    state$table, state$layer_priorities, names(state$authored_layer_order)
  )
  elements <- lapply(elements, function(element) {
    gid <- element$gid
    target <- state$layer_order_targets[[gid]]
    if (!is.null(target)) {
      edit <- layer_order[[gid]]
      original <- state$authored_layer_order[[as.character(target$slot)]][[as.character(target$index)]]
      original <- original %||% target$index
      element$layer_order <- list(current = edit %||% original)
      if (!is.null(edit)) element$layer_order$original <- original
      drawn <- paint[[as.character(target$slot)]]
      if (isTRUE(state$source_layer_order) && length(drawn)) {
        element$source_layer_order <- list(layer = target$index, facet = target$facet,
                                          priority = edit %||% original, drawn = drawn)
      }
    }
    element
  })
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72), elements = elements)
}
