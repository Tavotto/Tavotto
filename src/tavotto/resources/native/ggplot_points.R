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

ggplot_point_group_guide <- function(built, layer, mapped, members) {
  if (!"shape" %in% mapped || !identical(layer$show.legend, NA) ||
      !identical(as.list(layer$geom)$draw_key, ggplot2::draw_key_point)) return(NULL)
  guides <- built$plot$guides
  keys <- names(guides$params)[vapply(guides$params, function(p) length(intersect(p$aesthetic, mapped)) > 0L,
                                     logical(1L))]
  keys <- keys[!vapply(guides$guides[keys], inherits, logical(1L), "GuideColourbar")]
  if (length(keys) != 1L) return(NULL)
  guide <- guides$guides[[keys]]
  params <- guides$params[[keys]]
  key <- params$key
  decor <- params$decor$geom_point
  scale <- built$plot$scales$get_scales("shape")
  point_layers <- vapply(built$plot$layers, function(other) {
    identical(class(other$geom)[[1L]], "GeomPoint")
  }, logical(1L))
  if (sum(point_layers) != 1L || is.null(scale) || !isTRUE(scale$is_discrete()) ||
      !identical(params$aesthetic, "shape") || !"shape" %in% names(key) ||
      !identical(class(guide)[[1L]], "GuideLegend") || !is.function(decor$draw_key) ||
      "shape" %in% names(params$override.aes) || anyNA(key$.value) || anyDuplicated(key$.value) ||
      !is.character(key$.label) || anyNA(key$.label) || anyDuplicated(key$.label) ||
      !is.logical(decor$data$.draw) || anyNA(decor$data$.draw) ||
      length(decor$data$.draw) != nrow(key) ||
      !isTRUE(all.equal(as.numeric(decor$data$shape), as.numeric(key$shape)))) return(NULL)
  membership <- split(match(members$values, key$.value), members$panel)
  list(index = keys[[1L]], key = key, draw = decor$data$.draw, membership = membership)
}

ggplot_point_group_targets <- function(built, panels, draws, layer_index, guide) {
  if (is.null(guide) || !length(draws)) return(list())
  layout <- built$layout
  result <- list()
  for (draw in draws) {
    points <- draw$grob
    rows <- draw$rows
    id <- as.character(draw$panel_id)
    count <- nrow(rows)
    membership <- guide$membership[[id]]
    projected <- layout$coord$transform(rows[c("x", "y")], layout$panel_params[[draw$panel_id]])
    if (!inherits(points, "points") || !is.null(points$vp) ||
        length(points$x) != count || length(points$y) != count || length(membership) != count ||
        !isTRUE(all.equal(as.numeric(points$x), as.numeric(projected$x), tolerance = 1e-12)) ||
        !isTRUE(all.equal(as.numeric(points$y), as.numeric(projected$y), tolerance = 1e-12)) ||
        !isTRUE(all.equal(as.numeric(points$pch), as.numeric(rows$shape)))) return(list())
    groups <- split(seq_len(count), membership)
    suffix <- if (panels$single) "" else paste0(".panel.", id)
    for (key in as.integer(names(groups))) {
      marker <- ggplot_point_marker(guide$key$shape[[key]])
      if (is.null(marker) || !guide$draw[[key]]) next
      part <- groups[[as.character(key)]]
      if (!all(points$pch[part] == guide$key$shape[[key]])) return(list())
      context <- rlang::hash(list("GeomPoint", rows, draw$facet, guide$key$.value[[key]], part))
      result[[paste0("layer.", layer_index, suffix, ".scatter.group.", key)]] <- list(
        slot = draw$slot, child = draw$child, index = layer_index, count = count, facet = draw$facet,
        values = list(marker = marker), clip = identical(layout$coord$clip, "on"), context = context,
        group = list(rows = part, guide = guide$index, key = key, label = guide$key$.label[[key]])
      )
    }
  }
  groups <- split(names(result), vapply(result, function(part) part$group$key, integer(1L)))
  for (peers in groups) {
    if (length(peers) < 2L) next
    result[[peers[[1L]]]]$group$links <- peers[-1L]
    for (gid in peers[-1L]) result[[gid]]$group$links <- peers[[1L]]
  }
  result
}

ggplot_expand_point_groups <- function(state, edits) {
  names_edited <- intersect(names(edits), names(state$point_targets))
  by_group <- list()
  for (gid in names_edited) {
    target <- state$point_targets[[gid]]
    group <- target$group
    if (is.null(group)) next
    ggplot_point_values(target, edits[[gid]])
    prop <- edits[[gid]]$marker
    if (is.null(prop)) next
    key <- paste(group$guide, group$key, sep = "/")
    if (!is.null(by_group[[key]]) && !identical(by_group[[key]], prop)) {
      stop("A shared native R point marker cannot take conflicting values.")
    }
    by_group[[key]] <- prop
  }
  if (!length(by_group)) return(edits)
  for (gid in names(state$point_targets)) {
    group <- state$point_targets[[gid]]$group
    if (is.null(group)) next
    prop <- by_group[[paste(group$guide, group$key, sep = "/")]]
    if (!is.null(prop)) edits[[gid]]$marker <- prop
  }
  edits
}

ggplot_style_point_keys <- function(guide_params, targets, edits) {
  for (gid in intersect(names(edits), names(targets))) {
    group <- targets[[gid]]$group
    marker <- edits[[gid]]$marker
    if (is.null(group) || is.null(marker) || marker == "original") next
    shape <- ggplot_point_markers[[marker]]
    key <- group$key
    params <- guide_params[[group$guide]]
    params$key$shape[[key]] <- shape
    params$decor$geom_point$data$shape[[key]] <- shape
    guide_params[[group$guide]] <- params
  }
  guide_params
}

ggplot_point_targets <- function(built, panels, layer_grobs, maps) {
  if (is.null(panels)) return(list())
  layout <- built$layout
  targets <- list()
  for (index in seq_along(layer_grobs)) {
    layer <- built$plot$layers[[index]]
    if (!identical(class(layer$geom)[[1L]], "GeomPoint")) next
    mapped <- ggplot_styled_layer_mapping(layer)
    if (is.null(mapped)) next
    data <- built$data[[index]]
    drawn <- ggplot_panel_draws(panels, layer_grobs[[index]], data)
    if (ggplot_layer_has_legend_key(built, layer, mapped)) {
      capture <- ggplot_aligned_colour_capture(built, maps, index)
      values <- capture$input$shape
      if (length(values) == nrow(data) && identical(capture$output$shape, data$shape) &&
          length(drawn) == sum(!vapply(layer_grobs[[index]], inherits, logical(1L), "zeroGrob"))) {
        guide <- ggplot_point_group_guide(built, layer, mapped, list(values = values, panel = data$PANEL))
        targets <- c(targets, ggplot_point_group_targets(built, panels, drawn, index, guide))
      }
      next
    }
    for (draw in drawn) {
      points <- draw$grob
      rows <- draw$rows
      fields <- ggplot_point_fields(points, rows, mapped)
      if (!length(fields)) next
      if (!panels$single) {
        projected <- layout$coord$transform(rows[c("x", "y")], layout$panel_params[[draw$panel_id]])
        if (!isTRUE(all.equal(as.numeric(points$x), as.numeric(projected$x), tolerance = 1e-12)) ||
            !isTRUE(all.equal(as.numeric(points$y), as.numeric(projected$y), tolerance = 1e-12))) next
      }
      gid <- if (panels$single) paste0("layer.", index, ".scatter") else {
        paste0("layer.", index, ".panel.", draw$panel_id, ".scatter")
      }
      context <- if (panels$single) list("GeomPoint", rows) else list("GeomPoint", draw$facet, rows)
      targets[[gid]] <- list(
        slot = draw$slot, child = draw$child,
        index = index, count = nrow(rows), facet = draw$facet, values = fields,
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
  if (!is.null(edit$marker) && values$marker != "original") {
    marker <- ggplot_point_markers[[values$marker]]
    if (is.null(target$group)) {
      points$pch <- marker
    } else {
      points$pch <- rep_len(points$pch, target$count)
      points$pch[target$group$rows] <- marker
    }
  }
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
      rows <- target$group$rows %||% seq_len(target$count)
      center <- grid::deviceLoc(grid::unit(range(as.numeric(points$x[rows])), "native"),
                               grid::unit(range(as.numeric(points$y[rows])), "native"), valueOnly = TRUE)
      size <- rep_len(points$gp$fontsize + points$gp$lwd, target$count)
      radius <- max(size[rows]) / (2 * 72)
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
      } else if ((retain_clipped || !is.null(target$group)) && all(is.finite(c(lower, upper)))) {
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
    label <- ggplot_layer_label("\u6563\u70b9", target)
    group <- target$group
    if (!is.null(group)) label <- paste(label, group$label, sep = " \u00b7 ")
    alias <- if (length(group$links)) list(
      alias_covers = list(marker = list(marker = as.list(group$links))),
      alias_pure = list("marker")
    )
    c(list(gid = gid, role = "scatter", label = label,
         bbox = bbox, draggable = FALSE, editable = list(),
         point_series = list(context = target$context, current = ggplot_point_values(target, edit),
                             original = target$values, markers = as.list(names(ggplot_point_markers)),
                             edited = as.list(names(edit)))), alias)
  })
}
