# Apply paint to already drawn range polygons. Preserve both the sampled
# bounds and any separate stroke grobs produced by outline.type.

ggplot_ribbon_leaves <- function(grob) {
  if (!is.null(grob$vp)) return(NULL)
  if (inherits(grob, "zeroGrob")) return(list())
  if (inherits(grob, "polygon") || inherits(grob, "polyline")) return(list(grob))
  if (!identical(class(grob)[[1L]], "gTree")) return(NULL)
  children <- lapply(grob$children, ggplot_ribbon_leaves)
  if (any(vapply(children, is.null, logical(1L)))) return(NULL)
  unlist(children, recursive = FALSE, use.names = FALSE)
}

ggplot_ribbon_targets <- function(built, panels, layer_grobs) {
  if (is.null(panels)) return(list())
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    if (!kind %in% c("GeomRibbon", "GeomArea")) next
    mapped <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapped) || length(intersect(mapped, c("fill", "alpha"))) ||
        ggplot_layer_has_legend_key(built, layer, mapped)) next
    for (draw in ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])) {
      grob <- draw$grob
      suffix <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      leaves <- ggplot_ribbon_leaves(grob)
      polygons <- Filter(function(g) inherits(g, "polygon"), leaves)
      if (!length(polygons) || sum(vapply(polygons, function(g) length(g$x), integer(1L))) > 4096L) next
      ok <- vapply(polygons, function(g) {
        n <- length(g$x)
        n >= 3L && length(g$y) == n && is.null(g$id.lengths) &&
          (is.null(g$id) || (length(g$id) == n && !anyNA(g$id))) &&
          all(grid::unitType(g$x) == "native") && all(grid::unitType(g$y) == "native")
      }, logical(1L))
      if (!all(ok)) next
      pigments <- lapply(polygons, function(g) ggplot_uniform_pigment(g$gp$fill))
      face <- pigments[[1L]]
      if (is.null(face) || face$alpha <= 0 ||
          !all(vapply(pigments, identical, logical(1L), face))) next
      outlined <- any(vapply(leaves, function(g) {
        stroke <- ggplot_uniform_pigment(g$gp$col)
        # A nonuniform or unrecognized stroke is still an outline.
        is.null(stroke) || stroke$alpha > 0
      }, logical(1L)))
      facts <- list(kind, draw$rows, lapply(leaves, function(g) g$gp))
      if (!panels$single) facts <- c(facts, list(draw$facet))
      targets[[paste0("layer.", index, suffix, ".ribbon")]] <- list(
        index = index, slot = draw$slot, child = draw$child, grob_name = grob$name, face = face,
        facet = draw$facet,
        values = list(facecolor = face$rgb, alpha = if (!outlined) face$alpha),
        clip = identical(built$layout$coord$clip, "on"),
        context = rlang::hash(facts)
      )
    }
  }
  contexts <- vapply(targets, function(target) target$context, character(1L))
  targets[!(duplicated(contexts) | duplicated(contexts, fromLast = TRUE))]
}

ggplot_ribbon_values <- function(target, edit) {
  current <- Filter(Negate(is.null), target$values)
  for (prop in names(edit)) {
    value <- edit[[prop]]
    valid <- prop %in% names(current) && switch(prop,
      facecolor = is.character(value) && length(value) == 1L && !is.na(value) &&
        (value == "none" || grepl("^#[0-9A-Fa-f]{6}$", value)),
      alpha = is.numeric(value) && length(value) == 1L && is.finite(value) && value >= 0 && value <= 1,
      FALSE
    )
    if (!valid) stop("Invalid native R range fill style.")
    current[[prop]] <- value
  }
  current
}

ggplot_fill_ribbon <- function(grob, pigment) {
  if (inherits(grob, "polygon")) {
    grob$gp$fill <- pigment
  } else {
    for (i in seq_along(grob$children)) grob$children[[i]] <- ggplot_fill_ribbon(grob$children[[i]], pigment)
  }
  grob
}

ggplot_edit_ribbon <- function(table, target, edit) {
  current <- ggplot_ribbon_values(target, edit)
  panel <- table$grobs[[target$slot]]
  grob <- panel$children[[target$child]]
  if (!identical(grob$name, target$grob_name)) stop("The native R range fill changed.")
  pigment <- ggplot_edit_pigment(current$facecolor, target$face, edit$alpha, target$face$alpha)
  panel$children[[target$child]] <- ggplot_fill_ribbon(grob, pigment)
  table$grobs[[target$slot]] <- panel
  table
}

measure_ggplot_ribbons <- function(table, targets, frame_pt) {
  if (!length(targets)) return(list())
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  result <- list()
  for (gid in names(targets)) {
    target <- targets[[gid]]
    grob <- table$grobs[[target$slot]]$children[[target$child]]
    viewport <- ggplot_drawn_panel_viewport(drawn, table, target$slot, grob$name)
    if (is.null(viewport)) next
    result[[gid]] <- tryCatch({
      grid::downViewport(viewport, strict = TRUE)
      polygons <- Filter(function(g) inherits(g, "polygon"), ggplot_ribbon_leaves(grob))
      paths <- unlist(lapply(polygons, function(g) {
        ggplot_device_paths(g$x, g$y, g$id, TRUE, frame_pt)
      }), recursive = FALSE, use.names = FALSE)
      if (!length(paths)) stop("Invalid native R range geometry.")
      bbox <- ggplot_paths_bbox(paths)
      if (!all(is.finite(bbox)) || any(bbox[3:4] <= 0)) stop("Empty native R range geometry.")
      alpha <- ggplot_uniform_pigment(polygons[[1L]]$gp$fill)$alpha
      geometry <- list(kind = "multi_path", paths = paths, fill = alpha > 0, stroke = FALSE,
                       paint_order = target$child)
      if (alpha > 0) geometry$fill_alpha <- alpha
      if (target$clip) geometry$clip <- ggplot_panel_clip(frame_pt)
      list(bbox = as.list(bbox), geometry = geometry)
    }, error = function(error) NULL, finally = grid::upViewport(0))
  }
  result
}

ggplot_ribbon_elements <- function(state, edits, measured) {
  lapply(names(state$ribbon_targets), function(gid) {
    target <- state$ribbon_targets[[gid]]
    geometry <- if (is.null(measured)) state$ribbon_geometry[[gid]] else measured[[gid]]
    if (is.null(geometry)) stop("The native R range fill could not be measured.")
    edit <- edits[[gid]]
    c(list(gid = gid, role = "fill",
           label = ggplot_layer_label("\u8303\u56f4\u586b\u5145", target), draggable = FALSE, editable = list(),
           ribbon = list(context = target$context, current = ggplot_ribbon_values(target, edit),
                         original = ggplot_ribbon_values(target, NULL), edited = as.list(names(edit)))),
      geometry)
  })
}
