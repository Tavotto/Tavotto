ggplot_source_frame_panels <- function(facet, recipes) {
  force(facet)
  force(recipes)
  ggplot_clone(facet, draw_panels = function(self, panels, layout, x_scales, y_scales,
                                             ranges, coord, data, theme, params) {
    parent <- ggplot2::ggproto_parent(facet, self)
    table <- parent$draw_panels(panels, layout, x_scales, y_scales, ranges, coord, data, theme, params)
    fallback <- ggplot_frame_fallback(theme)
    if (is.null(fallback)) stop("Tavotto could not match the R frame theme.")
    single <- inherits(facet, "FacetNull")
    for (recipe in recipes) {
      row <- ggplot_facet_cell(layout, recipe$facet)
      if (is.null(row)) stop("Tavotto could not match the R frame facet.")
      slot <- if (single) match("panel", table$layout$name) else ggplot_facet_panel_slots(table, layout[row, ])
      if (is.na(slot)) stop("Tavotto could not find the R frame panel.")
      table <- ggplot_frame_edit(table, slot, fallback, recipe$style, panel_only = !single, authored = TRUE)
    }
    table
  })
}

ggplot_with_source_frames <- function(plot, recipes) {
  if (!ggplot_stock_frame_facet(plot$facet) ||
      !identical(class(plot$coordinates)[[1L]], "CoordCartesian")) {
    stop("Tavotto frames need the original cartesian layout.")
  }
  ggplot_facet_adjust(plot, "frame", recipes, ggplot_source_frame_panels)
}
