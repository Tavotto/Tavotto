ggplot_frame_visibility <- paste0("spine_", names(ggplot_frame_sides))
ggplot_frame_colors <- c("spine_color", paste0(ggplot_frame_visibility, "_color"))
ggplot_frame_widths <- c("spine_linewidth", paste0(ggplot_frame_visibility, "_linewidth"))
ggplot_frame_keys <- c(ggplot_frame_visibility, ggplot_frame_colors, ggplot_frame_widths)

ggplot_frame_target <- function(built, table, target, slot = ggplot_cartesian_panel_slot(built, table),
                                panel_only = FALSE, source_facet = NULL) {
  if (is.null(slot)) return(target)
  sources <- ggplot_frame_sources(table, slot, panel_only)
  if (is.null(sources)) return(target)
  fallback <- ggplot_frame_fallback(built$plot$theme)
  if (is.null(fallback)) return(target)
  styles <- ggplot_frame_model(sources, fallback, list())
  visible <- vapply(styles, function(edge) edge$show, logical(1L))
  preferred <- if (any(visible)) which(visible)[[1L]] else 1L
  base <- styles[[preferred]]
  target <- ggplot_panel_decoration(built, target, slot)
  source_rect <- if (is.null(sources$styles) && inherits(sources$border, "rect") &&
      !any(vapply(sources$axes, function(axis) !is.null(axis$line), logical(1L)))) {
    style <- ggplot2::calc_element("panel.border", built$plot$theme)
    if (identical(class(style), class(ggplot2::element_rect()))) list(style = style, grob = sources$border)
  }
  target$frame <- list(fallback = fallback, styles = styles, source_rect = source_rect,
                       original = list(spine_color = base$color %||% base$paint$rgb,
                                       spine_linewidth = base$width %||% base$paint$pt))
  if (ggplot_stock_panel_facet(built$layout$facet) &&
      identical(class(built$layout$coord)[[1L]], "CoordCartesian")) {
    target$source_frame <- list(facet = source_facet)
  }
  target
}

ggplot_frame_source_rect <- function(frame, edit) {
  native <- frame$source_rect
  if (is.null(native) || !all(names(edit) %in% c("spine_color", "spine_linewidth"))) return(NULL)
  stroke <- ggplot_frame_set_paint(native$grob, ggplot_frame_side_patch(edit, "top"))
  style <- native$style
  if (!is.null(edit$spine_color)) style$colour <- stroke$gp$col
  if (!is.null(edit$spine_linewidth)) style$linewidth <- stroke$gp$lwd / ggplot2::.pt
  style
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
    value[[paste0(key, "_color")]] <- patch$color %||% original$color %||% original$paint$rgb
    value[[paste0(key, "_linewidth")]] <- patch$width %||% original$width %||% original$paint$pt
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

ggplot_background_target <- function(built, table, target, slot = ggplot_cartesian_panel_slot(built, table),
                                     theme = built$plot$theme) {
  if (is.null(slot)) return(target)
  style <- ggplot2::calc_element("panel.background", theme)
  if (inherits(style, "element_blank")) {
    style <- ggplot2::element_rect(fill = NA, colour = NA, inherit.blank = FALSE)
  }
  if (!identical(class(style), class(ggplot2::element_rect()))) return(target)
  fill <- ggplot_uniform_pigment(style$fill)
  if (is.null(fill)) return(target)
  target <- ggplot_panel_decoration(built, target, slot)
  target$background <- list(style = style, original = fill$rgb)
  target
}

ggplot_background_style <- function(background, value) {
  if (is.null(background) || !is.character(value) || length(value) != 1L || is.na(value) ||
      (value != "none" && !grepl("^#[0-9A-Fa-f]{6}$", value))) {
    stop("Invalid native R panel background color.")
  }
  style <- background$style
  style$fill <- if (value == "none") NA_character_ else value
  style
}
