# Translate fixed point-series styles to the captured grid grob. A mapped
# aesthetic belongs to the data scale, even if its current values coincide.
ggplot_point_fields <- function(points, data, mapped) {
  if (!inherits(points, "points") || !is.null(points$vp) || !nrow(data) ||
      length(points$x) != nrow(data) || length(points$y) != nrow(data) ||
      !all(grid::unitType(points$x) == "native") || !all(grid::unitType(points$y) == "native") ||
      !all(is.finite(c(as.numeric(points$x), as.numeric(points$y))))) return(list())
  ggplot_point_style_fields(points, points$tavotto_point_alpha %||% data$alpha, mapped, seq_len(nrow(data)))
}

ggplot_point_style_fields <- function(points, alpha, mapped, indices) {
  select <- function(values) {
    if (length(values) <= 1L) values else values[(indices - 1L) %% length(values) + 1L]
  }
  base_size <- unique(round(select(points$gp$fontsize) - select(points$gp$lwd), 9))
  shape <- unique(select(points$pch))
  marker <- ggplot_point_marker(shape)
  channels <- ggplot_point_paint_channels(shape)
  paint <- list()
  for (prop in names(channels)) {
    channel <- channels[[prop]]
    aesthetic <- if (channel == "fill") "fill" else "colour"
    if (!aesthetic %in% mapped) {
      pigment <- ggplot_uniform_pigment(select(points$gp[[channel]]))
      paint[[prop]] <- pigment$rgb
    }
  }
  stroke <- unique(select(points$gp$lwd)) * ggplot_lwd_pt
  if ("edgecolor" %in% names(channels) && !"stroke" %in% mapped &&
      length(stroke) == 1L && is.finite(stroke) && stroke >= 0) paint$linewidth <- stroke
  alpha <- unique(select(alpha))
  used <- if (length(channels)) unname(channels) else c("col", "fill")
  pigments <- lapply(used, function(channel) select(points$gp[[channel]]))
  if (!all(vapply(pigments, function(values) {
    is.null(values) || is.character(values) || (is.logical(values) && all(is.na(values)))
  }, logical(1L)))) {
    alpha <- NULL
  } else if (length(alpha) == 1L && is.na(alpha)) {
    # Inherit the opacity of the pigments actually drawn in this series.
    pigments <- unique(unlist(pigments, use.names = FALSE))
    pigments <- pigments[!is.na(pigments)]
    if (length(pigments)) alpha <- unique(grDevices::col2rgb(pigments, alpha = TRUE)[4L, ]) / 255
  }
  c(Filter(Negate(is.null), list(
    size = if (!"size" %in% mapped && is_positive_ggplot_number(base_size)) base_size^2,
    marker = if (!"shape" %in% mapped) marker,
    alpha = if (!"alpha" %in% mapped && is.numeric(alpha) && length(alpha) == 1L &&
                is.finite(alpha) && alpha >= 0 && alpha <= 1) alpha
  )), paint)
}

ggplot_point_group_guide <- function(built, layer, mapped, maps, index) {
  if (!identical(layer$show.legend, NA) ||
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
  aesthetics <- params$aesthetic
  overrides <- names(params$override.aes)
  point_layers <- vapply(built$plot$layers, function(other) {
    identical(class(other$geom)[[1L]], "GeomPoint")
  }, logical(1L))
  if (sum(point_layers) != 1L || !length(aesthetics) ||
      !all(aesthetics %in% intersect(mapped, c("shape", "colour", "fill"))) ||
      !identical(class(guide)[[1L]], "GuideLegend") || !is.function(decor$draw_key) ||
      any(aesthetics %in% overrides) || anyNA(key$.value) || anyDuplicated(key$.value) ||
      !is.character(key$.label) || anyNA(key$.label) || anyDuplicated(key$.label) ||
      !is.logical(decor$data$.draw) || anyNA(decor$data$.draw) ||
      length(decor$data$.draw) != nrow(key) ||
      !all(vapply(aesthetics, function(channel) {
        channel %in% names(key) &&
          identical(as.character(decor$data[[channel]]), as.character(key[[channel]]))
      }, logical(1L)))) return(NULL)
  # Bind the guide key back to the observed values, even when two levels
  # deliberately use the same shape or pigment.
  memberships <- lapply(aesthetics, function(aesthetic) {
    groups <- ggplot_discrete_layer_groups(built, mapped, maps, index, aesthetic)
    if (is.null(groups)) return(NULL)
    rows <- match(groups$keys, key$.value)
    lapply(groups$membership, function(panel) rows[panel])
  })
  membership <- memberships[[1L]]
  if (is.null(membership) || !all(vapply(memberships, identical, logical(1L), membership))) return(NULL)
  # A guide override or a separate data scale still owns its presentation.
  mapped <- union(setdiff(mapped, intersect(aesthetics, "shape")), overrides)
  styles <- lapply(seq_len(nrow(key)), function(index) {
    data <- decor$data[index, , drop = FALSE]
    ggplot_point_style_fields(ggplot2::draw_key_point(data, list(), 1), data$alpha, mapped, 1L)
  })
  list(index = keys[[1L]], key = key, draw = decor$data$.draw,
       membership = membership, mapped = mapped, styles = styles, aesthetics = aesthetics)
}

ggplot_point_group_targets <- function(built, panels, draws, layer_index, guide) {
  if (is.null(guide) || !length(draws)) return(list())
  layout <- built$layout
  result <- list()
  for (draw in draws) {
    points <- draw$grob
    base <- points$tavotto_point_base %||% points
    rows <- draw$rows
    id <- as.character(draw$panel_id)
    count <- nrow(rows)
    membership <- guide$membership[[id]]
    projected <- layout$coord$transform(rows[c("x", "y")], layout$panel_params[[draw$panel_id]])
    if (!inherits(points, "points") || !is.null(points$vp) ||
        length(points$x) != count || length(points$y) != count || length(membership) != count ||
        !isTRUE(all.equal(as.numeric(points$x), as.numeric(projected$x), tolerance = 1e-12)) ||
        !isTRUE(all.equal(as.numeric(points$y), as.numeric(projected$y), tolerance = 1e-12)) ||
        !isTRUE(all.equal(as.numeric(base$pch), as.numeric(rows$shape)))) return(list())
    groups <- split(seq_len(count), membership)
    suffix <- if (panels$single) "" else paste0(".panel.", id)
    for (key in as.integer(names(groups))) {
      if (!guide$draw[[key]]) next
      part <- groups[[as.character(key)]]
      styles <- ggplot_point_style_fields(points, points$tavotto_point_alpha %||% rows$alpha, guide$mapped, part)
      key_style <- guide$styles[[key]]
      matches <- vapply(names(styles), function(prop) {
        isTRUE(all.equal(styles[[prop]], key_style[[prop]], tolerance = 1e-12))
      }, logical(1L))
      context <- list("GeomPoint", rows, draw$facet, guide$key$.value[[key]], part)
      if (!identical(guide$aesthetics, "shape")) context <- c(context, list(guide$aesthetics))
      result[[paste0("layer.", layer_index, suffix, ".scatter.group.", key)]] <- list(
        slot = draw$slot, child = draw$child, index = layer_index, count = count, facet = draw$facet,
        values = styles[matches],
        clip = identical(layout$coord$clip, "on"), context = rlang::hash(context),
        group = list(rows = part, guide = guide$index, key = key, label = guide$key$.label[[key]],
                     value = as.character(guide$key$.value[[key]]), aesthetics = guide$aesthetics)
      )
    }
  }
  groups <- split(names(result), vapply(result, function(part) part$group$key, integer(1L)))
  for (peers in groups) {
    if (length(peers) < 2L) next
    shared <- Reduce(intersect, lapply(result[peers], function(target) names(target$values)))
    for (gid in peers) result[[gid]]$values <- result[[gid]]$values[shared]
    result[[peers[[1L]]]]$group$links <- peers[-1L]
    for (gid in peers[-1L]) result[[gid]]$group$links <- peers[[1L]]
  }
  Filter(function(target) length(target$values) > 0L, result)
}

ggplot_expand_point_groups <- function(state, edits) {
  names_edited <- intersect(names(edits), names(state$point_targets))
  by_group <- list()
  for (gid in names_edited) {
    target <- state$point_targets[[gid]]
    group <- target$group
    if (is.null(group)) next
    ggplot_point_values(target, edits[[gid]])
    key <- paste(group$guide, group$key, sep = "/")
    for (prop in names(edits[[gid]])) {
      value <- edits[[gid]][[prop]]
      prior <- by_group[[key]][[prop]]
      if (!is.null(prior) && !identical(prior, value)) {
        stop("A shared native R point style cannot take conflicting values.")
      }
      by_group[[key]][[prop]] <- value
    }
  }
  if (!length(by_group)) return(edits)
  for (gid in names(state$point_targets)) {
    group <- state$point_targets[[gid]]$group
    if (is.null(group)) next
    changes <- by_group[[paste(group$guide, group$key, sep = "/")]]
    if (length(changes)) edits[[gid]] <- changes
  }
  edits
}

ggplot_style_point_keys <- function(guide_params, targets, edits) {
  for (gid in intersect(names(edits), names(targets))) {
    group <- targets[[gid]]$group
    if (is.null(group)) next
    patch <- edits[[gid]]
    key <- group$key
    params <- guide_params[[group$guide]]
    params <- ggplot_style_point_key(params, key, patch)
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
      if (length(drawn) == sum(!vapply(layer_grobs[[index]], inherits, logical(1L), "zeroGrob"))) {
        guide <- ggplot_point_group_guide(built, layer, mapped, maps, index)
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
      facecolor = , edgecolor = is.character(value) && length(value) == 1L &&
        !is.na(value) && (value == "none" || grepl("^#[0-9A-Fa-f]{6}$", value)),
      linewidth = is.numeric(value) && length(value) == 1L && is.finite(value) &&
        value >= 0 && value <= 8,
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
  rows <- target$group$rows
  points <- ggplot_style_point_grob(points, target$count, rows, values, edit, target$values$alpha)
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

ggplot_point_source_plan <- function(state, target, edit) {
  if (!length(edit)) return(NULL)
  if (!is.null(target$group)) {
    group <- target$group
    if (identical(edit$marker, "original")) return(NULL)
    ggplot_point_values(target, edit)
    geometry <- list(kind = "points", facet = NULL, values = edit,
                     category = list(value = group$value), aesthetics = as.list(group$aesthetics))
    return(ggplot_fixed_layer_source(state, target, edit, NULL, geometry))
  }
  points <- ggplot_point_grob(state$table$grobs[[target$slot]], target)
  channels <- ggplot_point_paint_channels(unique(points$pch))
  supported <- c("size", "alpha", "linewidth", names(channels))
  if (!identical(edit$marker, "original")) supported <- c(supported, "marker")
  if (!all(names(edit) %in% supported)) return(NULL)
  native <- list()
  if (!is.null(edit$size)) native$size <- ggplot_point_aesthetic_size(edit$size)
  if (!is.null(edit$alpha)) native$alpha <- edit$alpha
  if (!is.null(edit$marker)) native$shape <- ggplot_point_markers[[edit$marker]]
  if (!is.null(edit$linewidth)) native$stroke <- ggplot_point_aesthetic_stroke(edit$linewidth)
  for (prop in intersect(names(edit), names(channels))) {
    channel <- channels[[prop]]
    original <- ggplot_uniform_pigment(points$gp[[channel]])
    field <- if (channel == "fill") "fill" else "colour"
    native[[field]] <- ggplot_edit_pigment(edit[[prop]], original, edit$alpha, target$values$alpha)
  }
  ggplot_fixed_layer_source(state, target, edit, native)
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
    alias <- if (length(group$links)) {
      props <- names(target$values)
      covers <- lapply(props, function(prop) stats::setNames(list(as.list(group$links)), prop))
      list(alias_covers = stats::setNames(covers, props), alias_pure = as.list(props))
    }
    c(list(gid = gid, role = "scatter", label = label,
         bbox = bbox, draggable = FALSE, editable = list(),
         point_series = list(context = target$context, current = ggplot_point_values(target, edit),
                             source = ggplot_point_source_plan(state, target, edit),
                             original = target$values, markers = as.list(names(ggplot_point_markers)),
                             edited = as.list(names(edit)))), alias)
  })
}
