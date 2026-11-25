# Translate fixed point-series styles to the captured grid grob. A mapped
# aesthetic belongs to the data scale, even if its current values coincide.
ggplot_point_markers <- c("o" = 19L, "s" = 15L, "^" = 17L, "D" = 18L)

ggplot_point_marker <- function(pch) {
  if (length(pch) != 1L || !is.numeric(pch) || !is.finite(pch)) return(NULL)
  if (pch == 16L) pch <- 19L
  at <- match(pch, ggplot_point_markers)
  if (!is.na(at)) names(ggplot_point_markers)[[at]]
}

ggplot_point_targets <- function(built, table, layer_grobs) {
  slot <- which(table$layout$name == "panel")
  if (length(slot) != 1L || !identical(class(built$layout$coord)[[1L]], "CoordCartesian")) {
    return(list())
  }
  panel <- table$grobs[[slot]]
  guides <- built$plot$guides$params
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    if (!identical(class(layer$geom)[[1L]], "GeomPoint") || length(layer_grobs[[index]]) != 1L) next
    points <- layer_grobs[[index]][[1L]]
    data <- built$data[[index]]
    if (!inherits(points, "points") || !is.null(points$vp) || !nrow(data) ||
        length(points$x) != nrow(data) || length(points$y) != nrow(data) ||
        !all(grid::unitType(points$x) == "native") || !all(grid::unitType(points$y) == "native") ||
        !all(is.finite(c(as.numeric(points$x), as.numeric(points$y))))) next
    child <- which(panel$childrenOrder == points$name)
    if (length(child) != 1L) next
    mapping <- layer$computed_mapping
    if (any(vapply(mapping, function(value) {
      any(all.names(rlang::get_expr(value)) %in% c("after_scale", "stage"))
    }, logical(1L)))) next
    mapped <- setdiff(names(mapping), names(layer$aes_params))
    # A colour bar describes the scale, whereas a keyed legend draws the
    # series itself. Only the latter would become stale after a style edit.
    discrete_key <- any(vapply(seq_along(guides), function(i) {
      guide <- built$plot$guides$guides[[i]]
      !inherits(guide, "GuideColourbar") &&
        (length(intersect(guides[[i]]$aesthetic, mapped)) > 0L ||
         !identical(layer$show.legend, NA))
    }, logical(1L)))
    if (!identical(layer$show.legend, FALSE) && discrete_key) next
    base_size <- unique(round(points$gp$fontsize - points$gp$lwd, 9))
    marker <- ggplot_point_marker(unique(points$pch))
    alpha <- unique(data$alpha)
    if (length(alpha) == 1L && is.na(alpha)) {
      # Inherit the per-colour alpha unless every drawn pigment is opaque.
      pigments <- unique(c(points$gp$col, points$gp$fill))
      pigments <- pigments[!is.na(pigments)]
      alpha <- unique(grDevices::col2rgb(pigments, alpha = TRUE)[4L, ]) / 255
    }
    fields <- list(
      size = if (!"size" %in% mapped && is_positive_ggplot_number(base_size)) base_size^2,
      marker = if (!"shape" %in% mapped) marker,
      alpha = if (!"alpha" %in% mapped && is.numeric(alpha) && length(alpha) == 1L &&
                  is.finite(alpha) && alpha >= 0 && alpha <= 1) alpha
    )
    fields <- Filter(Negate(is.null), fields)
    if (!length(fields)) next
    gid <- paste0("layer.", index, ".scatter")
    targets[[gid]] <- list(slot = slot, child = child, index = index, count = nrow(data),
                           values = fields,
                           clip = identical(built$layout$coord$clip, "on"),
                           context = rlang::hash(list("GeomPoint", data)))
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

measure_ggplot_points <- function(table, targets, frame_pt) {
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
    path <- c(table$name, ggplot_cell_name(table$layout[target$slot, ]), panel$name)
    row <- which(drawn$name == points$name & drawn$gPath == paste(path, collapse = "::"))
    if (length(row) != 1L) next
    viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
    if (length(viewport) < 2L || viewport[[1L]] != "ROOT") next
    bbox <- tryCatch({
      grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
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
    list(gid = gid, role = "scatter", label = paste("\u6563\u70b9", target$index),
         bbox = bbox, draggable = FALSE, editable = list(),
         point_series = list(context = target$context, current = ggplot_point_values(target, edit),
                             original = target$values, markers = as.list(names(ggplot_point_markers)),
                             edited = as.list(names(edit))))
  })
}
