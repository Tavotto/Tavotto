ggplot_facet_grill <- function(panel) {
  child <- which(startsWith(panel$childrenOrder, "grill."))
  if (length(child) == 1L && inherits(panel$children[[child]], "gTree")) child
}

ggplot_replace_facet_grill <- function(table, slot, coord, range, theme, authored = FALSE) {
  panel <- table$grobs[[slot]]
  child <- ggplot_facet_grill(panel)
  if (is.null(child)) stop("The native R facet grid no longer matches its captured layout.")
  grill <- coord$render_bg(range, theme)
  grill$name <- panel$childrenOrder[[child]]
  if (authored) grill$tavotto_panel_theme <- theme
  panel$children[[child]] <- grill
  table$grobs[[slot]] <- panel
  table
}
