# The rectangle grob holds the computed stack/dodge arrangement. Recover bar
# centres in data units and check both ends against what the native geom drew.
ggplot_bar_span <- function(layout, data, rect) {
  ends <- c("xmin", "xmax")
  if (!identical(unique(data$flipped_aes), FALSE) || !all(ends %in% names(data)) ||
      !identical(as.list(layout$coord)$transform, as.list(ggplot2::CoordCartesian)$transform)) {
    return(NULL)
  }
  panel <- layout$panel_params[[1L]]
  project <- layout$coord$transform(data[ends], panel)
  lower <- as.numeric(data$xmin)
  upper <- as.numeric(data$xmax)
  if (!isTRUE(all.equal(as.numeric(project$xmin), as.numeric(rect$x), tolerance = 1e-12)) ||
      !isTRUE(all.equal(as.numeric(project$xmax - project$xmin),
                        as.numeric(rect$width), tolerance = 1e-12))) return(NULL)
  if (!isTRUE(panel$x$scale_is_discrete)) {
    transform <- panel$x$scale$get_transformation()
    if (!transform$name %in% names(ggplot_axis_modes)) return(NULL)
    lower <- transform$inverse(lower)
    upper <- transform$inverse(upper)
  }
  width <- upper - lower
  centre <- lower + width / 2
  if (any(!is.finite(width) | width <= 0 | !is.finite(centre))) return(NULL)
  list(centre = centre, width = width[[1L]])
}

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
    if (!any(as.numeric(rect$width) != 0 & as.numeric(rect$height) != 0)) next
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
    span <- ggplot_bar_span(built$layout, data, rect)
    fields <- list(
      facecolor = face$rgb,
      edgecolor = if (outline) edge$rgb,
      linewidth = if (outline) width,
      bar_width = span$width,
      alpha = if (length(visible_alpha) == 1L) visible_alpha
    )
    fields <- Filter(Negate(is.null), fields)
    gid <- paste0("layer.", index, ".bars")
    precise <- count <= remaining_paths
    if (precise) remaining_paths <- remaining_paths - count
    targets[[gid]] <- list(
      slot = slot, child = child, index = index, count = count, precise = precise,
      face = face, edge = edge, outline = outline, values = fields, centre = span$centre,
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
      bar_width = is_positive_ggplot_number(value),
      alpha = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 1,
      FALSE
    )
    if (!valid) stop("Invalid native R bar style.")
    values[[prop]] <- value
  }
  values
}

ggplot_edit_bar_width <- function(rect, centres, width, layout) {
  panel <- layout$panel_params[[1L]]
  lower <- centres - width / 2
  upper <- centres + width / 2
  if (!isTRUE(panel$x$scale_is_discrete)) {
    transform <- panel$x$scale$get_transformation()
    if (identical(transform$name, "log-10") && any(lower <= 0)) {
      stop("The native R bar width would cross zero on a logarithmic axis.")
    }
    lower <- transform$transform(lower)
    upper <- transform$transform(upper)
  }
  if (any(!is.finite(lower) | !is.finite(upper) | upper <= lower)) {
    stop("The native R bar width cannot be represented at the captured positions.")
  }
  project <- layout$coord$transform(data.frame(xmin = lower, xmax = upper), panel)
  x <- as.numeric(project$xmin)
  span <- as.numeric(project$xmax - project$xmin)
  if (any(!is.finite(x) | !is.finite(span) | span == 0)) {
    stop("The native R bar width cannot be represented in the current view.")
  }
  rect$x <- grid::unit(x, "native")
  rect$width <- grid::unit(span, "native")
  rect
}

ggplot_edit_bars <- function(table, target, edit, layout) {
  values <- ggplot_bar_values(target, edit)
  panel <- table$grobs[[target$slot]]
  rect <- ggplot_bar_rect(panel, target)
  if (!is.null(edit$bar_width)) {
    rect <- ggplot_edit_bar_width(rect, target$centre, values$bar_width, layout)
  }
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
      measured <- ggplot_bar_geometry(rect, target, frame_pt, retain_clipped)
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
