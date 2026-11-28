# One grid stroke conversion for native edits and generated R code.
ggplot_lwd_pt <- 72 / 96

ggplot_clone <- function(parent, ...) {
  force(parent)
  ggplot2::ggproto(NULL, parent, ...)
}

ggplot_facet_adjust <- function(plot, kind, recipes, install) {
  original <- plot$facet$tavotto_facet_base %||% plot$facet
  adjustments <- plot$facet$tavotto_facet_adjustments %||% list()
  previous <- adjustments[[kind]]$recipes
  if (kind == "tick_labels" && length(original$tavotto_tick_recipes)) {
    previous <- c(original$tavotto_tick_recipes, previous)
    original <- original$tavotto_tick_base
  }
  adjustments[[kind]] <- list(recipes = c(previous, recipes), install = install)
  facet <- original
  for (adjustment in adjustments) facet <- adjustment$install(facet, adjustment$recipes)
  plot$facet <- ggplot_clone(facet, tavotto_facet_base = original,
                             tavotto_facet_adjustments = adjustments)
  plot
}

ggplot_facet_cell <- function(cells, owner) {
  if (is.null(owner)) return(if (nrow(cells) == 1L) 1L)
  if (!length(owner) || !all(names(owner) %in% names(cells))) return(NULL)
  match <- which(vapply(seq_len(nrow(cells)), function(row) {
    identical(lapply(cells[row, names(owner), drop = FALSE], as.character), owner)
  }, logical(1L)))
  if (length(match) == 1L) match
}

ggplot_facet_group_id <- function(cells, axis, owner) {
  row <- ggplot_facet_cell(cells, owner)
  if (!is.null(row)) cells[[paste0("SCALE_", toupper(axis))]][[row]]
}

ggplot_facet_panel_slots <- function(table, cells) {
  match(paste("panel", cells$COL, cells$ROW, sep = "-"), table$layout$name)
}
