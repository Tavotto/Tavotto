ggplot_line_types <- c("-" = "solid", "--" = "dashed", ":" = "dotted", "-." = "dotdash")

ggplot_curve_key_name <- function(kind) {
  switch(kind, GeomLine = "geom_line", GeomPath = "geom_path")
}

ggplot_curve_stroke_rows <- function(line, rows, layout, panel) {
  if (!inherits(line, "polyline") || is.null(line$id)) return(NULL)
  rows$tavotto_row <- seq_len(nrow(rows))
  rows <- ggplot2::GeomPath$handle_na(rows, list(na.rm = TRUE))
  rows <- rows[order(rows$group), , drop = FALSE]
  projected <- layout$coord$transform(rows, layout$panel_params[[panel]])
  runs <- split(seq_len(nrow(projected)), projected$group)
  runs <- unname(runs[lengths(runs) >= 2L])
  draw <- projected[unlist(runs, use.names = FALSE), , drop = FALSE]
  if (!identical(as.integer(line$id), rep.int(seq_along(runs), lengths(runs))) ||
      !isTRUE(all.equal(as.numeric(line$x), as.numeric(draw$x), tolerance = 1e-12)) ||
      !isTRUE(all.equal(as.numeric(line$y), as.numeric(draw$y), tolerance = 1e-12))) return(NULL)
  lapply(runs, function(indices) projected$tavotto_row[indices])
}

ggplot_curve_style_strokes <- function(line, slots, patch, authored = FALSE) {
  count <- length(unique(line$id))
  base <- line$tavotto_curve_base %||% line
  current <- line$tavotto_curve_style %||% list()
  if (!is.null(patch$linewidth)) {
    width <- rep_len(line$gp$lwd, count)
    width[slots] <- patch$linewidth / ggplot_lwd_pt
    line$gp$lwd <- width
  }
  if (!is.null(patch$linestyle)) {
    style <- rep_len(line$gp$lty, count)
    if (is.numeric(style)) style <- unname(ggplot_line_types[style])
    style[slots] <- unname(ggplot_line_types[patch$linestyle])
    line$gp$lty <- style
  }
  if (!is.null(patch$alpha) || !is.null(patch$visible)) {
    visible <- (current$visible %||% rep(TRUE, count))[slots]
    visible[is.na(visible)] <- TRUE
    if (!is.null(patch$visible)) visible[] <- patch$visible
    original <- grDevices::col2rgb(rep_len(base$gp$col, count)[slots], alpha = TRUE)[4L, ] / 255
    opacity <- (current$alpha %||% rep(NA_real_, count))[slots]
    opacity[is.na(opacity)] <- original[is.na(opacity)]
    if (!is.null(patch$alpha)) opacity[] <- patch$alpha
    line$gp$col[slots] <- scales::alpha(rep_len(base$gp$col, count)[slots],
                                       ifelse(visible, opacity, 0))
  }
  if (authored) {
    for (property in names(patch)) {
      values <- current[[property]] %||% rep(NA, count)
      values[slots] <- patch[[property]]
      current[[property]] <- values
    }
    line$tavotto_curve_base <- base
    line$tavotto_curve_style <- current
  }
  line
}

ggplot_apply_curve_styles <- function(line, rows, layout, panel, values) {
  runs <- ggplot_curve_stroke_rows(line, rows, layout, panel)
  if (is.null(runs)) stop("Tavotto could not match the R curve geometry.")
  for (slot in seq_along(runs)) {
    patch <- lapply(values, function(value) unique(value[runs[[slot]]]))
    if (any(lengths(patch) != 1L)) stop("Tavotto found a mixed R curve category.")
    patch <- Filter(function(value) !is.na(value), patch)
    if (!is.null(patch$visible)) patch$visible <- as.logical(patch$visible)
    if (length(patch)) line <- ggplot_curve_style_strokes(line, slot, patch, authored = TRUE)
  }
  line
}

ggplot_curve_guide_style <- function(key, slots, patch) {
  if (!is.null(patch$linewidth)) key$linewidth[slots] <- patch$linewidth / ggplot_lwd_pt / ggplot2::.pt
  if (!is.null(patch$linestyle)) {
    if (is.numeric(key$linetype)) key$linetype <- unname(ggplot_line_types[key$linetype])
    key$linetype[slots] <- unname(ggplot_line_types[patch$linestyle])
  }
  if (!is.null(patch$alpha)) key$alpha[slots] <- patch$alpha
  key
}

ggplot_curve_guide_recipe <- function(kind, recipes) {
  key <- ggplot_curve_key_name(kind)
  change <- function(guide, row, patch) {
    original <- guide$decor[[key]]$data
    guide$decor[[key]]$data <- ggplot_curve_guide_style(original, row, patch)
    guide
  }
  list(key = key, aesthetics = "colour", recipes = recipes, apply = change)
}
