# Bind a recipe by actual facet values, then use the current native scale
# grouping. The selected panel may share that scale with other panels.
ggplot_facet_source_changes <- function(cells, recipes) {
  changes <- list()
  for (recipe in recipes) {
    axis <- recipe$axis
    id <- ggplot_facet_group_id(cells, axis, recipe$facet)
    if (is.null(id) || is.na(id)) stop("Tavotto could not match the R facet axis.")
    key <- as.character(id)
    changes[[axis]][[key]] <- utils::modifyList(changes[[axis]][[key]] %||% list(), recipe$view)
  }
  changes
}

ggplot_facet_source_groups <- function(layout, data, changes) {
  groups <- list()
  for (axis in names(changes)) {
    scales <- layout[[paste0("panel_scales_", axis)]]
    ids <- layout$layout[[paste0("SCALE_", toupper(axis))]]
    for (id in names(changes[[axis]])) {
      panel_ids <- layout$layout$PANEL[ids == as.integer(id)]
      scale <- scales[[as.integer(id)]]
      spec <- ggplot_projection_training(scale, data)
      if (is.null(spec)) stop("Tavotto could not match the R facet position scale.")
      rows <- lapply(data, function(layer) which(layer$PANEL %in% panel_ids))
      groups[[axis]][[id]] <- list(spec = spec, rows = rows)
    }
  }
  groups
}

ggplot_with_facet_views <- function(plot, recipes) {
  layout <- plot$layout
  if (!ggplot_source_facet_layout_supported(layout) ||
      !class(plot$facet)[[1L]] %in% c("FacetWrap", "FacetGrid") ||
      !identical(class(plot$coordinates)[[1L]], "CoordCartesian")) {
    stop("Tavotto facet views need the original cartesian layout.")
  }
  base <- layout$tavotto_facet_view_layout %||% layout
  recipes <- c(layout$tavotto_facet_view_recipes, recipes)
  changes_scale <- any(vapply(recipes, function(recipe) !is.null(recipe$view$mode), logical(1L)))
  if (changes_scale && !ggplot_source_facet_scale_supported(plot)) {
    stop("Tavotto facet scales need position-independent final aesthetics and stats.")
  }
  plot$layout <- ggplot_clone(base, tavotto_facet_view_layout = base,
                              tavotto_facet_view_recipes = recipes,
                              map_position = function(self, data) {
    mapped <- ggplot2::ggproto_parent(base, self)$map_position(data)
    # ggplot2 maps positions before and after stat/position calculation.
    # Only the second pass has panel parameters. Project at this point so
    # the normal guide training and non-position mappings see the final view.
    if (!length(self$panel_params)) return(mapped)
    changes <- ggplot_facet_source_changes(self$layout, recipes)
    groups <- ggplot_facet_source_groups(self, mapped, changes)
    ggplot_project_facet_positions(self, mapped, groups, changes)
  })
  plot
}
