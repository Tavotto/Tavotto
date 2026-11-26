# Translate fixed point-series styles to the captured grid grob. A mapped
# aesthetic belongs to the data scale, even if its current values coincide.
ggplot_point_markers <- c("o" = 19L, "s" = 15L, "^" = 17L, "D" = 18L)

ggplot_point_marker <- function(pch) {
  if (length(pch) != 1L || !is.numeric(pch) || !is.finite(pch)) return(NULL)
  if (pch == 16L) pch <- 19L
  at <- match(pch, ggplot_point_markers)
  if (!is.na(at)) names(ggplot_point_markers)[[at]]
}

ggplot_point_fields <- function(points, data, mapped) {
  if (!inherits(points, "points") || !is.null(points$vp) || !nrow(data) ||
      length(points$x) != nrow(data) || length(points$y) != nrow(data) ||
      !all(grid::unitType(points$x) == "native") || !all(grid::unitType(points$y) == "native") ||
      !all(is.finite(c(as.numeric(points$x), as.numeric(points$y))))) return(list())
  base_size <- unique(round(points$gp$fontsize - points$gp$lwd, 9))
  marker <- ggplot_point_marker(unique(points$pch))
  alpha <- unique(data$alpha)
  if (length(alpha) == 1L && is.na(alpha)) {
    # Inherit the per-colour alpha unless every drawn pigment is opaque.
    pigments <- unique(c(points$gp$col, points$gp$fill))
    pigments <- pigments[!is.na(pigments)]
    alpha <- unique(grDevices::col2rgb(pigments, alpha = TRUE)[4L, ]) / 255
  }
  Filter(Negate(is.null), list(
    size = if (!"size" %in% mapped && is_positive_ggplot_number(base_size)) base_size^2,
    marker = if (!"shape" %in% mapped) marker,
    alpha = if (!"alpha" %in% mapped && is.numeric(alpha) && length(alpha) == 1L &&
                is.finite(alpha) && alpha >= 0 && alpha <= 1) alpha
  ))
}

# Link each already drawn grob to its exact panel cell by native grob name.
# Facet wrap/grid can draw the same panel IDs in different row/column orders.
ggplot_panel_children <- function(table, slots) {
  children <- lapply(table$grobs[slots], function(grob) grob$childrenOrder)
  grobs <- unlist(children, use.names = FALSE)
  placements <- data.frame(grob = grobs, slot = rep(slots, lengths(children)),
                           child = sequence(lengths(children)))
  placements[!(duplicated(grobs) | duplicated(grobs, fromLast = TRUE)), , drop = FALSE]
}

ggplot_point_targets <- function(built, table, layer_grobs) {
  if (!identical(class(built$layout$coord)[[1L]], "CoordCartesian")) return(list())
  slots <- which(grepl("^panel($|-)", table$layout$name))
  if (!length(slots)) return(list())
  layout <- built$layout
  single <- identical(length(slots), 1L) && identical(table$layout$name[[slots]], "panel")
  facets <- setdiff(names(layout$layout), c("PANEL", "ROW", "COL", "SCALE_X", "SCALE_Y", "COORD"))
  if (!single && (!class(layout$facet)[[1L]] %in% c("FacetWrap", "FacetGrid") || !length(facets))) {
    return(list())
  }
  children <- ggplot_panel_children(table, slots)
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    if (!identical(class(layer$geom)[[1L]], "GeomPoint")) next
    mapped <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapped) || ggplot_layer_has_legend_key(built, layer, mapped)) next
    members <- layer_grobs[[index]]
    data <- built$data[[index]]
    groups <- if (is.null(names(members))) list(data) else split(data, data$PANEL)
    if (length(groups) != length(members) || !identical(names(groups), names(members))) next
    drawn_names <- vapply(members, function(member) member$name %||% "", character(1L))
    placements <- match(drawn_names, children$grob)
    for (i in seq_along(members)) {
      placement <- placements[[i]]
      if (is.na(placement)) next
      points <- members[[i]]
      rows <- groups[[i]]
      fields <- ggplot_point_fields(points, rows, mapped)
      if (!length(fields)) next
      facet <- NULL
      if (!single) {
        id <- unique(as.character(rows$PANEL))
        if (length(id) != 1L) next
        panel <- match(id, as.character(layout$layout$PANEL))
        if (is.na(panel)) next
        panel_id <- layout$layout$PANEL[[panel]]
        projected <- layout$coord$transform(rows[c("x", "y")], layout$panel_params[[panel_id]])
        if (!isTRUE(all.equal(as.numeric(points$x), as.numeric(projected$x), tolerance = 1e-12)) ||
            !isTRUE(all.equal(as.numeric(points$y), as.numeric(projected$y), tolerance = 1e-12))) next
        facet <- lapply(layout$layout[panel, facets, drop = FALSE], as.character)
      }
      gid <- if (single) paste0("layer.", index, ".scatter") else {
        paste0("layer.", index, ".panel.", panel_id, ".scatter")
      }
      context <- if (single) list("GeomPoint", rows) else list("GeomPoint", facet, rows)
      targets[[gid]] <- list(
        slot = children$slot[[placement]], child = children$child[[placement]],
        index = index, count = nrow(rows), facet = facet, values = fields,
        clip = identical(layout$coord$clip, "on"), context = rlang::hash(context))
    }
  }
  contexts <- vapply(targets, function(target) target$context, character(1L))
  targets[!(duplicated(contexts) | duplicated(contexts, fromLast = TRUE))]
}

ggplot_point_grob <- function(panel, target) {
  points <- panel$children[[target$child]]
  if (!inherits(points, "points") || length(points$x) != target$count ||
      length(points$y) != target$count) stop("The native R point series changed.")
  points
}

ggplot_point_values <- function(target, edit) {
  values <- target$values
  for (prop in names(edit)) {
    value <- edit[[prop]]
    valid <- prop %in% names(values) && switch(prop,
      size = is_positive_ggplot_number(value) && value >= 1 && value <= 400,
      marker = is.character(value) && length(value) == 1L &&
        value %in% c("original", names(ggplot_point_markers)),
      alpha = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 1,
      FALSE
    )
    if (!valid) stop("Invalid native R point style.")
    values[[prop]] <- value
  }
  values
}

ggplot_edit_points <- function(table, target, edit) {
  values <- ggplot_point_values(target, edit)
  panel <- table$grobs[[target$slot]]
  points <- ggplot_point_grob(panel, target)
  if (!is.null(edit$size)) points$gp$fontsize <- sqrt(values$size) + points$gp$lwd
  if (!is.null(edit$marker) && values$marker != "original") points$pch <- ggplot_point_markers[[values$marker]]
  if (!is.null(edit$alpha)) {
    points$gp$col <- scales::alpha(points$gp$col, values$alpha)
    points$gp$fill <- scales::alpha(points$gp$fill, values$alpha)
  }
  panel$children[[target$child]] <- points
  table$grobs[[target$slot]] <- panel
  table
}

measure_ggplot_points <- function(table, targets, frame_pt, retain_clipped = FALSE) {
  if (!length(targets)) return(list())
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  result <- list()
  frame <- frame_pt / 72
  for (gid in names(targets)) {
    target <- targets[[gid]]
    panel <- table$grobs[[target$slot]]
    points <- ggplot_point_grob(panel, target)
    viewport <- ggplot_drawn_panel_viewport(drawn, table, target$slot, points$name)
    if (is.null(viewport)) next
    bbox <- tryCatch({
      grid::downViewport(viewport, strict = TRUE)
      # The Cartesian viewport has no rotation. Transform the extrema only,
      # so the manifest size and device queries do not scale with point count.
      center <- grid::deviceLoc(grid::unit(range(as.numeric(points$x)), "native"),
                               grid::unit(range(as.numeric(points$y)), "native"), valueOnly = TRUE)
      radius <- max(points$gp$fontsize + points$gp$lwd) / (2 * 72)
      lower <- c(min(center$x) - radius, min(center$y) - radius)
      upper <- c(max(center$x) + radius, max(center$y) + radius)
      if (target$clip) {
        lo <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
        hi <- grid::deviceLoc(grid::unit(1, "npc"), grid::unit(1, "npc"), valueOnly = TRUE)
        lower <- pmax(lower, c(lo$x, lo$y))
        upper <- pmin(upper, c(hi$x, hi$y))
      }
      if (all(is.finite(c(lower, upper))) && all(upper > lower)) {
        as.list(c(lower[[1L]] / frame[[1L]], 1 - upper[[2L]] / frame[[2L]], (upper - lower) / frame))
      } else if (retain_clipped && all(is.finite(c(lower, upper)))) {
        # The same series may carry style overrides while outside the view.
        as.list(c(-1, -1, 0, 0))
      }
    }, error = function(error) NULL, finally = grid::upViewport(0))
    result[[gid]] <- bbox
  }
  result
}

ggplot_point_elements <- function(state, edits, measured) {
  lapply(names(state$point_targets), function(gid) {
    target <- state$point_targets[[gid]]
    bbox <- if (is.null(measured)) target$bbox else measured[[gid]]
    if (is.null(bbox)) stop("The native R point series could not be measured.")
    edit <- edits[[gid]]
    label <- paste("\u6563\u70b9", target$index)
    if (length(target$facet)) label <- paste(label, paste(unlist(target$facet), collapse = " / "), sep = " \u00b7 ")
    list(gid = gid, role = "scatter", label = label,
         bbox = bbox, draggable = FALSE, editable = list(),
         point_series = list(context = target$context, current = ggplot_point_values(target, edit),
                             original = target$values, markers = as.list(names(ggplot_point_markers)),
                             edited = as.list(names(edit))))
  })
}
