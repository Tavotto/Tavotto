# Apply the same draw-time recipe to named facets or categories in standalone R.
ggplot_facet_geometry <- function(recipes, row, categories) {
  local <- Filter(function(recipe) {
    facet <- recipe$facet
    !length(facet) || (all(names(facet) %in% names(row)) &&
      identical(lapply(row[names(facet)], as.character), facet))
  }, recipes)
  if (!length(local)) return(NULL)
  if (is.null(categories)) return(local[[1L]]$values)
  values <- list()
  for (recipe in local) {
    key <- recipe$category
    rows <- if (is.na(key)) which(is.na(categories)) else which(categories == key)
    if (!length(rows)) next
    for (property in names(recipe$values)) {
      if (is.null(values[[property]])) values[[property]] <- rep(NA_real_, length(categories))
      values[[property]][rows] <- recipe$values[[property]]
    }
  }
  if (length(values)) values
}

ggplot_set_layer_geometry <- function(layer, recipes, apply, lookup = NULL) {
  force(layer)
  force(recipes)
  force(apply)
  force(lookup)
  ggplot2::ggproto(NULL, layer, draw_geom = function(self, data, layout) {
    members <- ggplot2::ggproto_parent(layer, self)$draw_geom(data, layout)
    if (!nrow(data) || all(vapply(members, function(g) inherits(g, "zeroGrob"), logical(1L)))) {
      return(members)
    }
    categories <- if (!is.null(lookup)) lookup(data)
    positions <- seq_len(nrow(data))
    groups <- if (is.null(names(members))) list(positions) else split(positions, data$PANEL)
    if (length(groups) != length(members) || !identical(names(groups), names(members))) {
      stop("Tavotto could not match the R layer facets.")
    }
    Map(function(grob, positions) {
      if (inherits(grob, "zeroGrob")) return(grob)
      rows <- data[positions, , drop = FALSE]
      panel_id <- as.integer(as.character(rows$PANEL[[1L]]))
      cell <- layout$layout[as.integer(as.character(layout$layout$PANEL)) == panel_id, , drop = FALSE]
      values <- ggplot_facet_geometry(recipes, cell, categories[positions])
      if (is.null(values)) return(grob)
      apply(grob, rows, layout, panel_id, values)
    }, members, groups)
  })
}

ggplot_install_scoped_series_styles <- function(layer, recipes, apply, lookup) {
  scoped <- vapply(recipes, function(recipe) length(recipe$facet) > 0L, logical(1L))
  if (any(!scoped)) layer <- ggplot_set_layer_geometry(layer, recipes[!scoped], apply, lookup)
  if (any(scoped)) {
    force(apply)
    layer <- ggplot_set_layer_geometry(layer, recipes[scoped], function(grob, rows, layout, panel, values) {
      apply(grob, rows, layout, panel, values, local = TRUE)
    }, lookup)
  }
  layer
}

ggplot_source_styled_guides <- function(plot, styles) {
  original <- plot$guides
  plot$guides <- ggplot_clone(original, build = function(self, ...) {
    built <- ggplot2::ggproto_parent(original, self)$build(...)
    params <- built$params
    for (style in styles) {
      matches <- which(vapply(params, function(guide) {
        setequal(guide$aesthetic, style$aesthetics) && !is.null(guide$decor[[style$key]]$data)
      }, logical(1L)))
      if (length(matches) != 1L) stop("Tavotto could not match the R series guide.")
      guide <- params[[matches]]
      for (recipe in style$recipes) {
        row <- match(recipe$category, as.character(guide$key$.value))
        if (is.na(row)) stop("Tavotto could not match the R category in its guide.")
        guide <- style$apply(guide, row, recipe$values)
      }
      params[[matches]] <- guide
    }
    ggplot_clone(built, params = params)
  })
  plot
}
