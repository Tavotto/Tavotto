# Apply paint to already drawn range polygons. Preserve both the sampled
# bounds and any separate stroke grobs produced by outline.type.

ggplot_ribbon_target <- function(built, draw, members, index, kind, group = NULL) {
  grob <- draw$grob
  leaves <- unlist(lapply(members, ggplot_ribbon_leaves), recursive = FALSE, use.names = FALSE)
  polygons <- Filter(function(g) inherits(g, "polygon"), leaves)
  if (!length(polygons) || sum(vapply(polygons, function(g) length(g$x), integer(1L))) > 4096L) return(NULL)
  ok <- vapply(polygons, function(g) {
    n <- length(g$x)
    n >= 3L && length(g$y) == n && is.null(g$id.lengths) &&
      (is.null(g$id) || (length(g$id) == n && !anyNA(g$id))) &&
      all(grid::unitType(g$x) == "native") && all(grid::unitType(g$y) == "native")
  }, logical(1L))
  if (!all(ok)) return(NULL)
  pigments <- lapply(polygons, ggplot_ribbon_pigment)
  face <- pigments[[1L]]
  if (is.null(face) || !all(vapply(pigments, identical, logical(1L), face))) return(NULL)
  outlined <- any(vapply(leaves, function(g) {
    stroke <- ggplot_uniform_pigment(g$gp$col)
    # A nonuniform or unrecognized stroke is still an outline.
    is.null(stroke) || stroke$alpha > 0
  }, logical(1L)))
  if (!is.null(group) && outlined) return(NULL)
  facts <- list(kind, draw$rows, lapply(leaves, function(g) g$gp))
  if (length(draw$facet)) facts <- c(facts, list(draw$facet))
  if (!is.null(group)) facts <- c(facts, list(group$key, group$indices))
  list(index = index, slot = draw$slot, child = draw$child, grob_name = grob$name,
       face = face, facet = draw$facet, group = group,
       values = list(facecolor = if (is.null(group)) face$rgb, alpha = if (!outlined) face$alpha),
       clip = identical(built$layout$coord$clip, "on"), context = rlang::hash(facts))
}

ggplot_ribbon_targets <- function(built, panels, layer_grobs, maps) {
  if (is.null(panels)) return(list())
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    kind <- class(layer$geom)[[1L]]
    if (!kind %in% c("GeomRibbon", "GeomArea")) next
    mapped <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapped) || "alpha" %in% mapped) next
    groups <- NULL
    if ("fill" %in% mapped) {
      stat <- class(layer$stat)[[1L]]
      position <- class(layer$position)[[1L]]
      if (kind == "GeomRibbon" && (stat != "StatIdentity" || position != "PositionIdentity")) next
      if (kind == "GeomArea" && (!stat %in% c("StatAlign", "StatIdentity") ||
                                !position %in% c("PositionStack", "PositionIdentity"))) next
      methods <- c("draw_layer", "draw_panel", "draw_group", "handle_na")
      if (!identical(as.list(layer$geom)[methods], as.list(ggplot2::GeomRibbon)[methods])) next
      groups <- ggplot_discrete_layer_groups(built, mapped, maps, index, "fill")
      if (is.null(groups)) next
    } else if (ggplot_layer_has_legend_key(built, layer, mapped)) next
    for (draw in ggplot_panel_draws(panels, layer_grobs[[index]], built$data[[index]])) {
      suffix <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      gid <- paste0("layer.", index, suffix, ".ribbon")
      if (is.null(groups)) {
        targets[[gid]] <- ggplot_ribbon_target(built, draw, list(draw$grob), index, kind)
        next
      }
      parts <- ggplot_ribbon_members(draw, built$layout, groups)
      for (key in as.integer(names(parts))) {
        indices <- parts[[as.character(key)]]
        group <- list(key = groups$keys[[key]], label = groups$labels[[key]], indices = indices)
        targets[[paste0(gid, ".group.", key)]] <- ggplot_ribbon_target(
          built, draw, draw$grob$children[indices], index, kind, group)
      }
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

ggplot_edit_ribbon <- function(table, target, edit) {
  current <- ggplot_ribbon_values(target, edit)
  panel <- table$grobs[[target$slot]]
  grob <- panel$children[[target$child]]
  if (!identical(grob$name, target$grob_name)) stop("The native R range fill changed.")
  face <- current$facecolor %||% target$face$rgb
  pigment <- ggplot_edit_pigment(face, target$face, edit$alpha, target$face$alpha)
  if (is.null(target$group)) {
    grob <- ggplot_fill_ribbon(grob, pigment)
  } else {
    for (i in target$group$indices) grob$children[[i]] <- ggplot_fill_ribbon(grob$children[[i]], pigment)
  }
  panel$children[[target$child]] <- grob
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
    panel <- table$grobs[[target$slot]]
    grob <- panel$children[[target$child]]
    viewport <- ggplot_drawn_panel_viewport(drawn, table, target$slot, grob$name)
    if (is.null(viewport)) next
    result[[gid]] <- tryCatch({
      grid::downViewport(viewport, strict = TRUE)
      members <- if (is.null(target$group)) list(grob) else grob$children[target$group$indices]
      leaves <- unlist(lapply(members, ggplot_ribbon_leaves), recursive = FALSE, use.names = FALSE)
      polygons <- Filter(function(g) inherits(g, "polygon"), leaves)
      paths <- unlist(lapply(polygons, function(g) {
        ggplot_device_paths(g$x, g$y, g$id, TRUE, frame_pt)
      }), recursive = FALSE, use.names = FALSE)
      if (!length(paths)) stop("Invalid native R range geometry.")
      bbox <- ggplot_paths_bbox(paths)
      if (!all(is.finite(bbox)) || any(bbox[3:4] <= 0)) stop("Empty native R range geometry.")
      alpha <- ggplot_uniform_pigment(polygons[[1L]]$gp$fill)$alpha
      geometry <- list(kind = "multi_path", paths = paths, fill = alpha > 0, stroke = FALSE,
                       paint_order = match(grob$name, panel$childrenOrder))
      if (alpha > 0) geometry$fill_alpha <- alpha
      if (target$clip) geometry$clip <- ggplot_panel_clip(frame_pt)
      list(bbox = as.list(bbox), geometry = geometry)
    }, error = function(error) NULL, finally = grid::upViewport(0))
  }
  result
}

ggplot_ribbon_source_plan <- function(state, target, edit) {
  if (!length(edit)) return(NULL)
  ggplot_ribbon_values(target, edit)
  geometry <- list(kind = "fixed_ribbon", facet = target$facet, values = edit)
  if (!is.null(target$group)) {
    geometry$kind <- "ribbons"
    geometry$category <- list(value = as.character(target$group$key))
  }
  ggplot_fixed_layer_source(state, target, edit, NULL, geometry)
}

ggplot_ribbon_elements <- function(state, edits, measured) {
  lapply(names(state$ribbon_targets), function(gid) {
    target <- state$ribbon_targets[[gid]]
    geometry <- if (is.null(measured)) state$ribbon_geometry[[gid]] else measured[[gid]]
    if (is.null(geometry)) stop("The native R range fill could not be measured.")
    edit <- edits[[gid]]
    label <- ggplot_layer_label("\u8303\u56f4\u586b\u5145", target)
    if (!is.null(target$group)) label <- paste(label, target$group$label, sep = " \u00b7 ")
    c(list(gid = gid, role = "fill", label = label, draggable = FALSE, editable = list(),
           source_layer = ggplot_ribbon_source_plan(state, target, edit),
           ribbon = list(context = target$context, current = ggplot_ribbon_values(target, edit),
                         original = ggplot_ribbon_values(target, NULL), edited = as.list(names(edit)))),
      geometry)
  })
}
