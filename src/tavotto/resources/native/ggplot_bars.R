# Project rectangle styles onto captured bars. Their native data, statistics,
# orientation and position adjustments remain in the captured rectangle grob.
ggplot_bar_targets <- function(built, table, layer_grobs) {
  slot <- which(table$layout$name == "panel")
  if (length(slot) != 1L || !identical(class(built$layout$coord)[[1L]], "CoordCartesian")) {
    return(list())
  }
  panel <- table$grobs[[slot]]
  remaining_paths <- 500L
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    if (!kind %in% c("GeomBar", "GeomCol") || length(layer_grobs[[index]]) != 1L) next
    rect <- layer_grobs[[index]][[1L]]
    data <- built$data[[index]]
    count <- nrow(data)
    if (!inherits(rect, "rect") || !is.null(rect$vp) || !count ||
        !identical(rect$just, c("left", "top")) || !is.null(rect$hjust) || !is.null(rect$vjust)) next
    dimensions <- rect[c("x", "y", "width", "height")]
    if (!all(vapply(dimensions, function(u) length(u) == count &&
                    all(grid::unitType(u) == "native") && all(is.finite(as.numeric(u))), logical(1L)))) next
    if (!any(as.numeric(rect$width) > 0 & as.numeric(rect$height) > 0)) next
    child <- which(panel$childrenOrder == rect$name)
    if (length(child) != 1L) next
    mapped <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapped) || ggplot_layer_has_legend_key(built, layer, mapped)) next
    if (length(intersect(mapped, c("fill", "colour", "alpha", "size", "linewidth", "linetype")))) next
    face <- ggplot_uniform_pigment(rect$gp$fill)
    edge <- ggplot_uniform_pigment(rect$gp$col)
    width <- unique(rect$gp$lwd) * ggplot_lwd_pt
    lty <- unique(rect$gp$lty)
    if (is.null(face) || is.null(edge) || length(width) != 1L ||
        !is.finite(width) || width < 0 || length(lty) != 1L || is.na(lty)) next
    outline <- !(lty %in% c("0", "blank"))
    visible_alpha <- unique(c(face$alpha, edge$alpha[outline && width > 0]))
    visible_alpha <- visible_alpha[visible_alpha > 0]
    if (!length(visible_alpha)) next
    fields <- list(
      facecolor = face$rgb,
      edgecolor = if (outline) edge$rgb,
      linewidth = if (outline) width,
      alpha = if (length(visible_alpha) == 1L) visible_alpha
    )
    fields <- Filter(Negate(is.null), fields)
    gid <- paste0("layer.", index, ".bars")
    precise <- count <= remaining_paths
    if (precise) remaining_paths <- remaining_paths - count
    targets[[gid]] <- list(
      slot = slot, child = child, index = index, count = count, precise = precise,
      face = face, edge = edge, outline = outline, values = fields,
      clip = identical(built$layout$coord$clip, "on"),
      context = rlang::hash(list(kind, data, rect$gp))
    )
  }
  contexts <- vapply(targets, function(target) target$context, character(1L))
  targets[!(duplicated(contexts) | duplicated(contexts, fromLast = TRUE))]
}

ggplot_bar_rect <- function(panel, target) {
  rect <- panel$children[[target$child]]
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
      alpha = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 1,
      FALSE
    )
    if (!valid) stop("Invalid native R bar style.")
    values[[prop]] <- value
  }
  values
}

ggplot_edit_bars <- function(table, target, edit) {
  values <- ggplot_bar_values(target, edit)
  panel <- table$grobs[[target$slot]]
  rect <- ggplot_bar_rect(panel, target)
  if (!is.null(edit$linewidth)) rect$gp$lwd <- values$linewidth / ggplot_lwd_pt
  if (!is.null(edit$facecolor) || !is.null(edit$alpha)) {
    rect$gp$fill <- ggplot_edit_pigment(values$facecolor, target$face, edit$alpha, target$values$alpha)
  }
  if (!is.null(values$edgecolor) && (!is.null(edit$edgecolor) || !is.null(edit$alpha))) {
    rect$gp$col <- ggplot_edit_pigment(values$edgecolor, target$edge, edit$alpha, target$values$alpha)
  }
  panel$children[[target$child]] <- rect
  table$grobs[[target$slot]] <- panel
  table
}

ggplot_bar_geometry <- function(rect, target, frame_pt) {
  x0 <- as.numeric(rect$x)
  x1 <- x0 + as.numeric(rect$width)
  y1 <- as.numeric(rect$y)
  y0 <- y1 - as.numeric(rect$height)
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
  face <- ggplot_uniform_pigment(rect$gp$fill)$alpha
  stroke_pt <- if (target$outline && ggplot_uniform_pigment(rect$gp$col)$alpha > 0) {
    unique(rect$gp$lwd) * ggplot_lwd_pt
  } else 0
  pad <- stroke_pt / (2 * frame_pt)
  content_lo <- c(min(left), min(top))
  content_hi <- c(max(right), max(bottom))
  lo <- content_lo - pad
  hi <- content_hi + pad
  clip <- NULL
  if (target$clip) {
    clip <- ggplot_bbox(grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE),
                       grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE), frame_pt)
    if (!all(pmin(content_hi, clip[1:2] + clip[3:4]) > pmax(content_lo, clip[1:2]))) return(NULL)
    lo <- pmax(lo, clip[1:2])
    hi <- pmin(hi, clip[1:2] + clip[3:4])
  }
  if (!all(is.finite(c(lo, hi))) || !all(hi > lo)) return(NULL)
  result <- list(bbox = as.list(c(lo, hi - lo)))
  if (target$precise) {
    paths <- Map(function(l, r, t, b) list(points = list(c(l, t), c(r, t), c(r, b), c(l, b)),
                                            closed = TRUE), left, right, top, bottom)
    result$geometry <- list(kind = "multi_path", paths = paths, fill = face > 0,
                            stroke = stroke_pt > 0, stroke_pt = stroke_pt)
    if (face > 0) result$geometry$fill_alpha <- face
    if (length(clip)) result$geometry$clip <- as.list(clip)
  }
  result
}

measure_ggplot_bars <- function(table, targets, frame_pt) {
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
      measured <- ggplot_bar_geometry(rect, target, frame_pt)
      if (!is.null(measured$geometry)) measured$geometry$paint_order <- match(rect$name, panel$childrenOrder)
      measured
    }, error = function(error) NULL, finally = grid::upViewport(0))
  }
  result
}

ggplot_bar_elements <- function(state, edits, measured) {
  lapply(names(state$bar_targets), function(gid) {
    target <- state$bar_targets[[gid]]
    geometry <- if (is.null(measured)) state$bar_geometry[[gid]] else measured[[gid]]
    if (is.null(geometry)) stop("The native R bar series could not be measured.")
    edit <- edits[[gid]]
    c(list(gid = gid, role = "bar_series", label = paste("\u67f1\u5f62\u7cfb\u5217", target$index),
           draggable = FALSE, editable = list(), bar_series = list(
             context = target$context, current = ggplot_bar_values(target, edit),
             original = target$values, edited = as.list(names(edit))
           )), geometry)
  })
}
