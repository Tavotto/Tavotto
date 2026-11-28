ggplot_source_panel_themes <- function(facet, recipes) {
  force(facet)
  force(recipes)
  ggplot_clone(facet, draw_panels = function(self, panels, layout, x_scales, y_scales,
                                             ranges, coord, data, theme, params) {
    parent <- ggplot2::ggproto_parent(facet, self)
    table <- parent$draw_panels(panels, layout, x_scales, y_scales, ranges, coord, data, theme, params)
    styles <- list()
    for (recipe in recipes) {
      row <- ggplot_facet_cell(layout, recipe$facet)
      if (is.null(row)) stop("Tavotto could not match the R panel theme facet.")
      key <- as.character(row)
      styles[[key]] <- (styles[[key]] %||% theme) + recipe$theme
    }
    for (key in names(styles)) {
      row <- as.integer(key)
      slot <- ggplot_facet_panel_slots(table, layout[row, ])
      panel <- as.integer(as.character(layout$PANEL[[row]]))
      if (is.na(slot) || is.na(panel)) stop("Tavotto could not find the themed R panel.")
      table <- ggplot_replace_facet_grill(table, slot, coord, ranges[[panel]], styles[[key]], authored = TRUE)
    }
    table
  })
}

ggplot_with_panel_themes <- function(plot, recipes) {
  if (inherits(plot$facet, "FacetNull") || !ggplot_stock_panel_facet(plot$facet) ||
      !identical(as.list(plot$coordinates)$render_bg, as.list(ggplot2::CoordCartesian)$render_bg)) {
    stop("Tavotto panel themes need the original cartesian facets.")
  }
  ggplot_facet_adjust(plot, "panel_theme", recipes, ggplot_source_panel_themes)
}
