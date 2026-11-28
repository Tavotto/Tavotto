# A mapped palette may vary the RGB values while every rectangle has the
# same opacity. Keep the actual palette and use that opacity for hit testing.
ggplot_bar_alphas <- function(colour) {
  if (!length(colour) ||
      !(is.character(colour) || (is.logical(colour) && all(is.na(colour))))) return(NULL)
  unname(grDevices::col2rgb(colour, alpha = TRUE)[4L, ]) / 255
}

ggplot_bar_visible_alphas <- function(rect, outline) {
  fill <- ggplot_bar_alphas(unique(rect$gp$fill))
  stroke <- ggplot_bar_alphas(unique(rect$gp$col))
  if (is.null(fill) || is.null(stroke)) return(NULL)
  values <- unique(c(fill[fill > 0], if (outline) stroke[stroke > 0]))
  if (length(values)) values else 0
}

ggplot_bar_target <- function(layout, draw, fixed) {
  rect <- ggplot_bar_native_rect(draw$grob)
  count <- nrow(draw$rows)
  if (!inherits(rect, "rect") || !is.null(rect$vp) || !count ||
      !identical(rect$just, c("left", "top")) || !is.null(rect$hjust) || !is.null(rect$vjust)) return(NULL)
  dimensions <- rect[c("x", "y", "width", "height")]
  if (!all(vapply(dimensions, function(u) length(u) == count &&
                  all(grid::unitType(u) == "native") && all(is.finite(as.numeric(u))), logical(1L)))) return(NULL)
  if (!any(as.numeric(rect$width) != 0 & as.numeric(rect$height) != 0)) return(NULL)
  face <- ggplot_uniform_pigment(rect$gp$fill)
  edge <- ggplot_uniform_pigment(rect$gp$col)
  width <- unique(rect$gp$lwd) * ggplot_lwd_pt
  lty <- unique(rect$gp$lty)
  if (length(width) != 1L || !is.finite(width) || width < 0 || length(lty) != 1L || is.na(lty)) return(NULL)
  outline <- !(lty %in% c("0", "blank"))
  visible_alpha <- ggplot_bar_visible_alphas(rect, outline && width > 0)
  if (is.null(visible_alpha) ||
      (all(visible_alpha == 0) && is.null(attr(rect, "tavotto_bar_alpha", exact = TRUE)))) return(NULL)
  span <- ggplot_bar_span(layout, draw$panel_id, draw$rows, rect, names(ggplot_axis_modes))
  size <- if (is.null(span$width)) list() else stats::setNames(list(span$width), span$prop)
  fields <- if (fixed && !is.null(face) && !is.null(edge)) {
    c(list(
      facecolor = face$rgb,
      edgecolor = if (outline) edge$rgb,
      linewidth = if (outline) width
    ), size, list(alpha = if (length(visible_alpha) == 1L) visible_alpha))
  } else if (isTRUE(span$uniform)) size
  if (fixed && !is.null(face)) {
    fields <- c(fields, ggplot_bar_hatch_fields(rect, seq_len(count), draw$rows))
  }
  fields <- Filter(Negate(is.null), fields)
  if (!length(fields) && is.null(span)) return(NULL)
  list(
    slot = draw$slot, child = draw$child, panel_id = draw$panel_id, facet = draw$facet, count = count,
    face = face, edge = edge, outline = outline, values = fields, span = span,
    hatch_rows = draw$rows["alpha"], hatch_edge = ggplot_bar_hatch_edge(rect),
    clip = identical(layout$coord$clip, "on")
  )
}

ggplot_bar_group_targets <- function(target, groups, parent_gid, rect, allow_alpha) {
  if (is.null(groups) || is.null(target$span)) return(list())
  membership <- groups$membership[[as.character(target$panel_id)]]
  if (length(membership) != target$count) return(list())
  indices <- split(seq_len(target$count), membership)
  # A facet can contain one category that has peers elsewhere in the plot.
  # Its local controls still preserve the shared legend and the other facets.
  if (!length(indices) || (length(indices) == 1L && is.null(target$facet))) return(list())
  result <- list()
  prop <- target$span$prop
  for (index in as.integer(names(indices))) {
    part <- target
    rows <- indices[[as.character(index)]]
    width <- ggplot_common_bar_width(target$span$widths[rows])
    if (is.null(width)) next
    part$group <- list(rows = rows, label = groups$labels[[index]], parent = parent_gid,
                       value = as.character(groups$keys[[index]]))
    part$span$centre <- target$span$centre[rows]
    part$span$rows <- rows
    part$values <- stats::setNames(list(width), prop)
    selected <- ggplot_bar_group_rect(rect, part)
    part$hatch_edge <- ggplot_bar_hatch_edge(selected)
    pigment <- ggplot_uniform_pigment(selected$gp$fill)
    if (!is.null(pigment)) {
      part$values$facecolor <- pigment$rgb
      part$values <- c(part$values, ggplot_bar_hatch_fields(rect, rows, target$hatch_rows))
    }
    if (allow_alpha) {
      opacity <- ggplot_bar_visible_alphas(selected, target$outline && any(selected$gp$lwd > 0))
      if (length(opacity) == 1L) part$values$alpha <- opacity
    }
    part$context <- rlang::hash(list(target$context, groups$keys[[index]], rows))
    result[[paste0(parent_gid, ".group.", index)]] <- part
  }
  result
}

ggplot_bar_targets <- function(built, panels, layer_grobs, maps) {
  if (is.null(panels)) return(list())
  layout <- built$layout
  remaining_paths <- 500L
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    if (!kind %in% c("GeomBar", "GeomCol")) next
    mapped <- ggplot_styled_layer_mapping(layer)
    groups <- ggplot_discrete_layer_groups(built, mapped, maps, index, "fill")
    fixed <- !is.null(mapped) && !ggplot_layer_has_legend_key(built, layer, mapped) &&
      !length(intersect(mapped, c("fill", "colour", "alpha", "size", "linewidth", "linetype")))
    for (draw in ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])) {
      target <- ggplot_bar_target(layout, draw, fixed)
      if (is.null(target)) next
      suffix <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      gid <- paste0("layer.", index, suffix, ".bars")
      rect <- ggplot_bar_native_rect(draw$grob)
      context <- list(kind, draw$rows, rect$gp)
      if (!panels$single) context <- c(context, list(draw$facet))
      target$index <- index
      target$context <- rlang::hash(context)
      subgroups <- ggplot_bar_group_targets(target, groups, gid, rect, !"alpha" %in% mapped)
      if (!length(target$values) && !length(subgroups)) next
      target$precise <- target$count <= remaining_paths
      if (target$precise) remaining_paths <- remaining_paths - target$count
      targets[[gid]] <- target
      for (part_gid in names(subgroups)) {
        part <- subgroups[[part_gid]]
        part_count <- length(part$group$rows)
        part$precise <- part_count <= remaining_paths
        if (part$precise) remaining_paths <- remaining_paths - part_count
        targets[[part_gid]] <- part
      }
    }
  }
  contexts <- vapply(targets, function(target) target$context, character(1L))
  targets[!(duplicated(contexts) | duplicated(contexts, fromLast = TRUE))]
}

ggplot_bar_rect <- function(panel, target) {
  rect <- ggplot_bar_native_rect(panel$children[[target$child]])
  if (!inherits(rect, "rect") || length(rect$x) != target$count) {
    stop("The native R bar series changed.")
  }
  rect
}

ggplot_bar_values <- function(target, edit) {
  values <- target$values
  for (prop in names(edit)) {
    value <- edit[[prop]]
    valid <- prop %in% names(values) && switch(prop,
      facecolor = , edgecolor = is.character(value) && length(value) == 1L &&
        !is.na(value) && (value == "none" || grepl("^#[0-9A-Fa-f]{6}$", value)),
      linewidth = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 5,
      hatch = is.character(value) && length(value) == 1L && !is.na(value) &&
        value %in% c("", names(ggplot_bar_hatch_catalog$patterns)),
      hatchcolor = is.character(value) && length(value) == 1L && !is.na(value) &&
        (value == "edge" || grepl("^#[0-9A-Fa-f]{6}$", value)),
      hatch_linewidth = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= ggplot_bar_hatch_catalog$linewidth_pt[[1L]] &&
        value <= ggplot_bar_hatch_catalog$linewidth_pt[[2L]],
      bar_width = , bar_thickness = is_positive_ggplot_number(value),
      alpha = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 1,
      FALSE
    )
    if (!valid) stop("Invalid native R bar style.")
    values[[prop]] <- value
  }
  values
}

ggplot_edit_bars <- function(table, target, edit, layout) {
  values <- ggplot_bar_values(target, edit)
  panel <- table$grobs[[target$slot]]
  rect <- ggplot_bar_rect(panel, target)
  if (!is.null(target$span) && !is.null(edit[[target$span$prop]])) {
    span <- target$span
    view <- layout$panel_params[[target$panel_id]] %||% span$panel
    coord <- layout$coord %||% span$coord
    rect <- ggplot_edit_bar_span(rect, span, values[[span$prop]], view, coord)
  }
  if (is.null(target$group)) {
    if (!is.null(edit$linewidth)) rect$gp$lwd <- values$linewidth / ggplot_lwd_pt
    if (!is.null(edit$facecolor) || !is.null(edit$alpha)) {
      rect$gp$fill <- ggplot_edit_pigment(values$facecolor, target$face, edit$alpha, target$values$alpha)
    }
    if (!is.null(values$edgecolor) && (!is.null(edit$edgecolor) || !is.null(edit$alpha))) {
      rect$gp$col <- ggplot_edit_pigment(values$edgecolor, target$edge, edit$alpha, target$values$alpha)
    }
  } else {
    rows <- target$group$rows
    if (!is.null(edit$facecolor)) rect <- ggplot_edit_bar_fill(rect, rows, edit$facecolor)
    if (!is.null(edit$alpha)) rect <- ggplot_edit_bar_alpha(rect, rows, edit$alpha)
  }
  if (any(names(edit) %in% ggplot_bar_hatch_props) ||
      (!is.null(edit$alpha) && !is.null(attr(rect, "tavotto_bar_hatching", exact = TRUE)))) {
    rows <- target$group$rows %||% seq_len(target$count)
    rect <- ggplot_record_bar_hatch(rect, rows, edit, target$hatch_rows, ggplot_bar_hatch_catalog)
  }
  panel$children[[target$child]] <- ggplot_draw_bar_hatching(rect)
  table$grobs[[target$slot]] <- panel
  table
}

ggplot_bar_group_rect <- function(rect, target) {
  rows <- target$group$rows
  if (is.null(rows)) return(rect)
  for (field in c("x", "y", "width", "height")) rect[[field]] <- rect[[field]][rows]
  for (field in names(rect$gp)) {
    if (length(rect$gp[[field]]) > 1L) {
      rect$gp[[field]] <- rep_len(rect$gp[[field]], target$count)[rows]
    }
  }
  rect
}

ggplot_bar_geometry <- function(rect, target, frame_pt, retain_clipped = FALSE) {
  # Native ggplot2 rectangles carry signed dimensions on reversed axes.
  x <- as.numeric(rect$x)
  y <- as.numeric(rect$y)
  x_end <- x + as.numeric(rect$width)
  y_end <- y - as.numeric(rect$height)
  x0 <- pmin(x, x_end)
  x1 <- pmax(x, x_end)
  y0 <- pmin(y, y_end)
  y1 <- pmax(y, y_end)
  if (!target$precise) {
    x0 <- min(x0)
    x1 <- max(x1)
    y0 <- min(y0)
    y1 <- max(y1)
  }
  frame <- frame_pt / 72
  lower <- grid::deviceLoc(grid::unit(x0, "native"), grid::unit(y0, "native"), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::unit(x1, "native"), grid::unit(y1, "native"), valueOnly = TRUE)
  left <- lower$x / frame[[1L]]
  right <- upper$x / frame[[1L]]
  top <- 1 - upper$y / frame[[2L]]
  bottom <- 1 - lower$y / frame[[2L]]
  pigments <- if (target$precise) identity else unique
  fill_alpha <- ggplot_bar_alphas(pigments(rect$gp$fill))
  stroke_alpha <- ggplot_bar_alphas(pigments(rect$gp$col))
  face <- max(fill_alpha)
  stroke_pt <- if (target$outline && any(stroke_alpha > 0)) unique(rect$gp$lwd) * ggplot_lwd_pt else 0
  pad <- stroke_pt / (2 * frame_pt)
  content_lo <- c(min(left), min(top))
  content_hi <- c(max(right), max(bottom))
  lo <- content_lo - pad
  hi <- content_hi + pad
  clip <- NULL
  if (target$clip) {
    clip <- ggplot_bbox(grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE),
                       grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE), frame_pt)
    if (!all(pmin(content_hi, clip[1:2] + clip[3:4]) > pmax(content_lo, clip[1:2]))) {
      if (retain_clipped) return(list(bbox = as.list(c(-1, -1, 0, 0))))
      return(NULL)
    }
    lo <- pmax(lo, clip[1:2])
    hi <- pmin(hi, clip[1:2] + clip[3:4])
  }
  if (!all(is.finite(c(lo, hi))) || !all(hi > lo)) return(NULL)
  result <- list(bbox = as.list(c(lo, hi - lo)))
  if (target$precise) {
    paths <- Map(function(l, r, t, b) list(points = list(c(l, t), c(r, t), c(r, b), c(l, b)),
                                            closed = TRUE), left, right, top, bottom)
    count <- length(paths)
    painted <- rep_len(fill_alpha, count) > 0
    if (stroke_pt > 0) painted <- painted | rep_len(stroke_alpha, count) > 0
    if (any(painted)) paths <- paths[painted]
    result$geometry <- list(kind = "multi_path", paths = paths, fill = face > 0,
                            stroke = stroke_pt > 0, stroke_pt = stroke_pt)
    if (face > 0) result$geometry$fill_alpha <- face
    if (length(clip)) result$geometry$clip <- as.list(clip)
  }
  result
}

measure_ggplot_bars <- function(table, targets, frame_pt, retain_clipped = FALSE) {
  if (!length(targets)) return(list())
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    panel <- table$grobs[[target$slot]]
    rect <- ggplot_bar_rect(panel, target)
    viewport <- ggplot_drawn_panel_viewport(drawn, table, target$slot, rect$name)
    if (is.null(viewport)) next
    result[[gid]] <- tryCatch({
      grid::downViewport(viewport, strict = TRUE)
      measured <- ggplot_bar_geometry(ggplot_bar_group_rect(rect, target), target, frame_pt, retain_clipped)
      if (!is.null(measured$geometry)) measured$geometry$paint_order <- match(rect$name, panel$childrenOrder)
      measured
    }, error = function(error) NULL, finally = grid::upViewport(0))
  }
  result
}

ggplot_bar_source_plan <- function(state, target, edit, edits) {
  paint <- c("facecolor", "edgecolor", "alpha", "linewidth", ggplot_bar_hatch_props)
  if (!length(edit) || !all(names(edit) %in% c(paint, target$span$prop))) return(NULL)
  if (!is.null(target$group)) {
    changed <- state$bar_targets[intersect(names(state$bar_targets), names(edits))]
    if (!all(names(edit) %in% c(target$span$prop, "alpha", "facecolor", ggplot_bar_hatch_props)) || any(vapply(changed, function(other) {
      identical(other$index, target$index) && is.null(other$group)
    }, logical(1L)))) return(NULL)
    geometry <- list(kind = "bars", facet = target$facet, values = edit,
                     category = list(value = target$group$value))
    return(ggplot_fixed_layer_source(state, target, edit, NULL, geometry))
  }
  if (ggplot_layer_has_group_edits(state$bar_targets, target$index, edits)) return(NULL)
  values <- ggplot_bar_values(target, edit)
  aesthetics <- edit[intersect("alpha", names(edit))]
  if (!is.null(edit$facecolor)) {
    aesthetics$fill <- ggplot_edit_pigment(values$facecolor, target$face, edit$alpha, target$values$alpha)
  }
  if (!is.null(edit$edgecolor)) {
    aesthetics$colour <- ggplot_edit_pigment(values$edgecolor, target$edge, edit$alpha, target$values$alpha)
  }
  if (!is.null(edit$linewidth)) aesthetics$linewidth <- edit$linewidth / (ggplot_lwd_pt * ggplot2::.pt)
  prop <- target$span$prop %||% "bar_width"
  geometry_fields <- edit[intersect(c(prop, ggplot_bar_hatch_props), names(edit))]
  geometry <- if (length(geometry_fields)) list(kind = "bars", facet = target$facet, values = geometry_fields)
  ggplot_fixed_layer_source(state, target, edit, aesthetics, geometry)
}

ggplot_bar_elements <- function(state, edits, measured) {
  lapply(names(state$bar_targets), function(gid) {
    target <- state$bar_targets[[gid]]
    geometry <- if (is.null(measured)) state$bar_geometry[[gid]] else measured[[gid]]
    if (is.null(geometry)) stop("The native R bar series could not be measured.")
    edit <- edits[[gid]]
    label <- ggplot_layer_label("\u67f1\u5f62\u7cfb\u5217", target)
    effective <- edit
    if (!is.null(target$group)) {
      label <- paste(label, target$group$label, sep = " \u00b7 ")
      inherited <- edits[[target$group$parent]][names(target$values)]
      effective <- utils::modifyList(Filter(Negate(is.null), inherited) %||% list(), edit %||% list())
    }
    element <- c(list(gid = gid, role = "bar_series", label = label,
                      draggable = FALSE, editable = list(), bar_series = list(
                        context = target$context, current = ggplot_bar_values(target, effective),
                        original = target$values, edited = as.list(names(edit)),
                        hatch_choices = as.list(names(ggplot_bar_hatch_catalog$patterns)),
                        hatch_edge = target$hatch_edge
                      )), geometry)
    element$source_layer <- ggplot_bar_source_plan(state, target, edit, edits)
    element
  })
}
