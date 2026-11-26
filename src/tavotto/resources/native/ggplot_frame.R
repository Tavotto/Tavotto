`%||%` <- function(value, default) if (is.null(value)) default else value

ggplot_frame_sides <- c(top = "axis-t", right = "axis-r", bottom = "axis-b", left = "axis-l")
ggplot_frame_endpoints <- list(top = c(0, 1, 1, 1), right = c(1, 0, 1, 1),
                               bottom = c(0, 0, 1, 0), left = c(0, 0, 0, 1))

ggplot_frame_paint <- function(grob) {
  pigment <- ggplot_uniform_pigment(grob$gp$col)
  width <- grob$gp$lwd
  if (is.null(pigment) || length(width) != 1L || !is.finite(width) || width < 0) return(NULL)
  list(rgb = pigment$rgb, alpha = pigment$alpha, pt = width * ggplot_lwd_pt)
}

ggplot_frame_sources <- function(table, slot) {
  panel <- table$grobs[[slot]]
  border_index <- which(startsWith(panel$childrenOrder, "panel.border"))
  if (length(border_index) != 1L) return(NULL)
  border <- panel$children[[border_index]]
  if (inherits(border, "rect")) {
    dimension <- border[c("x", "y", "width", "height")]
    centered <- all(vapply(dimension, function(unit) {
      identical(grid::unitType(unit), "npc") && length(unit) == 1L
    }, logical(1L))) && identical(unname(vapply(dimension, as.numeric, numeric(1L))),
                                  c(.5, .5, 1, 1))
    if (!centered || !is.null(border$vp) || !identical(border$just, "centre") ||
        !is.null(border$hjust) || !is.null(border$vjust) || is.null(ggplot_frame_paint(border))) return(NULL)
  } else if (!inherits(border, "zeroGrob")) return(NULL)
  axes <- lapply(ggplot_frame_sides, function(cell) {
    row <- which(table$layout$name == cell)
    if (length(row) != 1L) return(NULL)
    axis <- table$grobs[[row]]
    if (inherits(axis, "zeroGrob")) return(list(row = row))
    if (!inherits(axis, "absoluteGrob") || length(axis$children) != 2L ||
        !identical(names(axis$children)[[2L]], "axis")) return(NULL)
    line <- axis$children[[1L]]
    if (inherits(line, "zeroGrob")) return(list(row = row))
    if (!inherits(line, "polyline") || !is.null(line$arrow) || is.null(ggplot_frame_paint(line))) return(NULL)
    list(row = row, line = line)
  })
  if (any(lengths(axes) == 0L)) return(NULL)
  list(index = border_index, border = border, axes = axes)
}

ggplot_frame_side_patch <- function(edit, side) {
  key <- paste0("spine_", side)
  list(show = edit[[key]],
       color = edit[[paste0(key, "_color")]] %||% edit$spine_color,
       width = edit[[paste0(key, "_linewidth")]] %||% edit$spine_linewidth)
}

ggplot_frame_model <- function(sources, fallback, edit) {
  border <- if (inherits(sources$border, "rect")) sources$border
  styles <- lapply(names(sources$axes), function(side) {
    line <- sources$axes[[side]]$line
    origin <- if (!is.null(border)) border else if (!is.null(line)) line else fallback
    paint <- ggplot_frame_paint(origin)
    patch <- ggplot_frame_side_patch(edit, side)
    patch$show <- patch$show %||% (!is.null(border) || !is.null(line))
    patch$paint <- paint
    patch
  }) |> stats::setNames(names(sources$axes))
  styles
}

ggplot_frame_set_paint <- function(grob, style) {
  if (!is.null(style$color)) {
    paint <- ggplot_frame_paint(grob)
    # Retain each native line's encoded opacity when changing its color.
    value <- if (style$color == "none") NA_character_ else style$color
    grob$gp$col <- scales::alpha(value, paint$alpha)
  }
  if (!is.null(style$width)) grob$gp$lwd <- style$width / ggplot_lwd_pt
  grob
}

ggplot_frame_edit <- function(table, slot, fallback, edit) {
  sources <- ggplot_frame_sources(table, slot)
  if (is.null(sources)) stop("The native R frame no longer matches its captured layout.")
  styles <- ggplot_frame_model(sources, fallback, edit)
  panel <- table$grobs[[slot]]
  has_border <- inherits(sources$border, "rect")
  visible <- vapply(styles, function(style) style$show, logical(1L))
  overlay <- visible & (has_border | vapply(sources$axes, function(axis) is.null(axis$line), logical(1L)))
  if (has_border || any(overlay)) {
    painted <- lapply(styles[overlay], function(style) {
      ggplot_frame_set_paint(if (has_border) sources$border else fallback, style)
    })
    if (has_border && all(overlay) && all(vapply(painted, function(edge) {
      identical(edge$gp, painted[[1L]]$gp)
    }, logical(1L)))) {
      panel$children[[sources$index]] <- painted[[1L]]
    } else {
      edges <- Map(function(grob, side) {
        x <- ggplot_frame_endpoints[[side]]
        grid::segmentsGrob(x[[1L]], x[[2L]], x[[3L]], x[[4L]], gp = grob$gp,
                           name = paste0("tavotto.frame.", side))
      }, painted, names(painted))
      border <- grid::gTree(children = do.call(grid::gList, unname(edges)), name = sources$border$name)
      panel$children[[sources$index]] <- border
    }
    table$grobs[[slot]] <- panel
  }
  for (side in names(sources$axes)) {
    source <- sources$axes[[side]]
    if (is.null(source$line)) next
    axis <- table$grobs[[source$row]]
    style <- styles[[side]]
    axis$children[[1L]] <- if (style$show) ggplot_frame_set_paint(source$line, style) else
      grid::nullGrob(name = source$line$name)
    table$grobs[[source$row]] <- axis
  }
  table
}

ggplot_frame_visibility <- paste0("spine_", names(ggplot_frame_sides))
ggplot_frame_colors <- c("spine_color", paste0(ggplot_frame_visibility, "_color"))
ggplot_frame_widths <- c("spine_linewidth", paste0(ggplot_frame_visibility, "_linewidth"))
ggplot_frame_keys <- c(ggplot_frame_visibility, ggplot_frame_colors, ggplot_frame_widths)

ggplot_frame_target <- function(built, table, target) {
  slot <- ggplot_cartesian_panel_slot(built, table)
  if (is.null(slot)) return(target)
  sources <- ggplot_frame_sources(table, slot)
  if (is.null(sources)) return(target)
  line <- ggplot2::calc_element("line", built$plot$theme)
  if (!identical(class(line), class(ggplot2::element_line()))) return(target)
  fallback <- ggplot2::element_grob(line, x = c(0, 1), y = c(0, 0))
  if (is.null(ggplot_frame_paint(fallback))) return(target)
  styles <- ggplot_frame_model(sources, fallback, list())
  visible <- vapply(styles, function(edge) edge$show, logical(1L))
  preferred <- if (any(visible)) which(visible)[[1L]] else 1L
  base <- styles[[preferred]]$paint
  target <- ggplot_panel_decoration(built, target, slot)
  target$frame <- list(fallback = fallback, styles = styles,
                       original = list(spine_color = base$rgb, spine_linewidth = base$pt))
  target
}

ggplot_frame_value <- function(frame, edit) {
  value <- frame$original
  names_changed <- intersect(names(edit), names(value))
  if (length(names_changed)) value[names_changed] <- edit[names_changed]
  for (side in names(frame$styles)) {
    original <- frame$styles[[side]]
    key <- paste0("spine_", side)
    patch <- ggplot_frame_side_patch(edit, side)
    value[[key]] <- patch$show %||% original$show
    value[[paste0(key, "_color")]] <- patch$color %||% original$paint$rgb
    value[[paste0(key, "_linewidth")]] <- patch$width %||% original$paint$pt
  }
  value
}

ggplot_validate_frame_edits <- function(frame, edit) {
  if (is.null(frame)) stop("The requested native R frame edit is unavailable.")
  for (prop in names(edit)) {
    value <- edit[[prop]]
    valid <- if (prop %in% ggplot_frame_visibility) {
      is.logical(value) && length(value) == 1L && !is.na(value)
    } else if (prop %in% ggplot_frame_colors) {
      is.character(value) && length(value) == 1L && !is.na(value) &&
        (value == "none" || grepl("^#[0-9A-Fa-f]{6}$", value))
    } else {
      is_positive_ggplot_number(value) && value >= .1 && value <= 10
    }
    if (!valid) stop("Invalid native R frame style.")
  }
}
