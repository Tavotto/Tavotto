# Apply the same draw-time recipe to the named facet in a standalone R script.
ggplot_facet_geometry <- function(recipes, row) {
  for (recipe in recipes) {
    facet <- recipe$facet
    if (!length(facet) ||
        (all(names(facet) %in% names(row)) && identical(lapply(row[names(facet)], as.character), facet))) {
      return(recipe$values)
    }
  }
  NULL
}

ggplot_set_layer_geometry <- function(layer, recipes, apply) {
  force(layer)
  force(recipes)
  force(apply)
  ggplot2::ggproto(NULL, layer, draw_geom = function(self, data, layout) {
    members <- ggplot2::ggproto_parent(layer, self)$draw_geom(data, layout)
    if (!nrow(data) || all(vapply(members, function(g) inherits(g, "zeroGrob"), logical(1L)))) {
      return(members)
    }
    groups <- if (is.null(names(members))) list(data) else split(data, data$PANEL)
    if (length(groups) != length(members) || !identical(names(groups), names(members))) {
      stop("Tavotto could not match the R layer facets.")
    }
    Map(function(grob, rows) {
      if (inherits(grob, "zeroGrob")) return(grob)
      panel_id <- as.integer(as.character(rows$PANEL[[1L]]))
      cell <- layout$layout[as.integer(as.character(layout$layout$PANEL)) == panel_id, , drop = FALSE]
      values <- ggplot_facet_geometry(recipes, cell)
      if (is.null(values)) return(grob)
      apply(grob, rows, layout, panel_id, values)
    }, members, groups)
  })
}
