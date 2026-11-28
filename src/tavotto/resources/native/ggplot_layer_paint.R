ggplot_set_layer_paint <- function(layer, recipes) {
  if (length(layer$tavotto_facet_paint)) {
    return(ggplot_clone(layer, tavotto_facet_paint = c(layer$tavotto_facet_paint, recipes)))
  }
  force(layer)
  ggplot_clone(layer, tavotto_facet_paint = recipes, draw_geom = function(self, data, layout) {
    paint <- list()
    for (recipe in self$tavotto_facet_paint) {
      row <- ggplot_facet_cell(layout$layout, recipe$facet)
      if (is.null(row)) stop("Tavotto could not match the R layer facet.")
      panel <- as.character(layout$layout$PANEL[[row]])
      paint[[panel]] <- utils::modifyList(paint[[panel]] %||% list(), recipe$aesthetics)
    }
    for (panel in names(paint)) {
      rows <- which(as.character(data$PANEL) == panel)
      if (!length(rows)) next
      for (field in names(paint[[panel]])) {
        value <- paint[[panel]][[field]]
        if (field == "linetype" && is.numeric(data[[field]])) value <- match(value, ggplot_line_types)
        data[[field]][rows] <- value
      }
    }
    members <- ggplot2::ggproto_parent(layer, self)$draw_geom(data, layout)
    if (identical(class(layer$geom)[[1L]], "GeomPoint")) {
      for (panel in intersect(names(members), names(paint))) {
        if (!is.null(paint[[panel]]$alpha)) members[[panel]]$tavotto_point_alpha <- paint[[panel]]$alpha
      }
    }
    members
  })
}
