ggplot_drawn_layer_name <- function(grob) {
  if (is.null(grob$name) || inherits(grob, "zeroGrob")) NA_character_ else grob$name
}

ggplot_order_layer_children <- function(panel, indices, changes) {
  members <- names(indices)
  positions <- match(members, panel$childrenOrder)
  if (anyNA(positions) || anyDuplicated(members) || anyDuplicated(indices)) {
    stop("Tavotto could not match the native R panel layer order.")
  }
  priority <- indices
  for (key in names(changes)) {
    member <- match(as.integer(key), indices)
    if (is.na(member)) stop("The native R layer could not be found for reordering.")
    priority[[member]] <- changes[[key]]
  }
  # Reorder drawing only. Keep structural child slots, viewports and grid layers.
  panel$childrenOrder[sort(positions)] <- members[order(priority, indices)]
  panel
}

ggplot_layer_order_recipes <- function(cells, recipes) {
  groups <- list()
  for (recipe in recipes) {
    row <- ggplot_facet_cell(cells, recipe$facet)
    if (is.null(row)) stop("Tavotto could not match the R layer's facet.")
    panel <- as.character(cells$PANEL[[row]])
    groups[[panel]][[as.character(recipe$layer)]] <- recipe$priority
  }
  groups
}

ggplot_source_layer_order <- function(facet, recipes) {
  force(facet)
  force(recipes)
  ggplot_clone(facet, draw_panel_content = function(self, panels, layout, ...) {
    parent <- ggplot2::ggproto_parent(facet, self)
    drawn <- parent$draw_panel_content(panels, layout, ...)
    groups <- ggplot_layer_order_recipes(layout, recipes)
    for (key in names(groups)) {
      index <- as.integer(key)
      members <- vapply(panels, function(layer) ggplot_drawn_layer_name(layer[[index]]), character(1L))
      indices <- which(!is.na(members))
      indices <- stats::setNames(indices, members[indices])
      drawn[[index]] <- ggplot_order_layer_children(drawn[[index]], indices, groups[[key]])
    }
    drawn
  })
}

ggplot_stock_layer_order_facet <- function(facet) {
  if (!ggplot_stock_panel_facet(facet)) return(FALSE)
  base <- facet$tavotto_facet_base %||% facet$tavotto_tick_base %||% facet
  kind <- class(base)[[1L]]
  identical(as.list(base)$draw_panel_content, as.list(getExportedValue("ggplot2", kind))$draw_panel_content)
}

ggplot_with_layer_order <- function(plot, recipes) {
  if (!ggplot_stock_layer_order_facet(plot$facet) ||
      !identical(class(plot$coordinates)[[1L]], "CoordCartesian")) {
    stop("Tavotto layer ordering needs the original Cartesian facets.")
  }
  ggplot_facet_adjust(plot, "layer_order", recipes, ggplot_source_layer_order)
}
