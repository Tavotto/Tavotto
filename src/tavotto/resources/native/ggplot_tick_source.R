# A label follows its native break and owning side. Locator and formatter
# edits can change the visible keys, so ordinal indices are drawing-only.
ggplot_tick_members <- function(panel, index, prefix) {
  guide <- panel$guides$guides[[index]]
  if (!inherits(guide, "GuideAxis")) return(NULL)
  params <- panel$guides$params[[index]]
  key <- params$key
  if (!is.data.frame(key) || !nrow(key) || !is.character(key$.label)) return(NULL)
  major <- if (is.null(key$.type)) rep(TRUE, nrow(key)) else key$.type == "major"
  rendered <- if (isTRUE(params$tavotto_minor_labels)) seq_len(nrow(key)) else which(major)
  values <- key$.value[rendered]
  scale <- panel[[params$aesthetic]]$scale
  if (length(values) != length(rendered) || anyNA(key$.label[rendered]) || is.null(scale)) return(NULL)
  if (isTRUE(scale$is_discrete())) {
    values <- as.character(values)
  } else {
    native <- as.numeric(scale$get_transformation()$inverse(values))
    if (any(!is.finite(native))) return(NULL)
    native[native == 0] <- 0
    values <- sprintf("%.15g", native)
  }
  identifiers <- vapply(values, rlang::hash, character(1L), USE.NAMES = FALSE)
  if (anyDuplicated(identifiers)) return(NULL)
  list(rows = rendered, gids = paste0(prefix, identifiers), breaks = values, major = major[rendered],
       labels = key$.label[rendered])
}

ggplot_clone <- function(parent, ...) {
  force(parent)
  ggplot2::ggproto(NULL, parent, ...)
}

ggplot_relabel_tick_members <- function(panels, scopes, edits) {
  originals <- list()
  hidden <- character()
  for (gid in names(edits)) {
    scope <- scopes[[gid]]
    panel <- panels[[scope$panel]]
    members <- ggplot_tick_members(panel, scope$index, scope$prefix)
    if (is.null(members)) next
    params <- panel$guides$params
    guide <- params[[scope$index]]
    selected <- which(members$major & members$gids %in% names(edits[[gid]]))
    for (index in selected) {
      member <- members$gids[[index]]
      originals[[member]] <- members$labels[[index]]
      guide$key$.label[[members$rows[[index]]]] <- edits[[gid]][[member]]$text
    }
    if (length(selected)) {
      if (!any(nzchar(guide$key$.label[members$rows]))) hidden <- c(hidden, gid)
      params[[scope$index]] <- guide
      panel$guides <- ggplot_clone(panel$guides, params = params)
      panels[[scope$panel]] <- panel
    }
  }
  list(panels = panels, originals = originals, hidden = hidden)
}


# Find a recipe's axis using the actual facet variables and the trained key.
ggplot_source_tick_edits <- function(panels, cells, recipes, scopes = NULL) {
  if (!length(recipes)) return(list(scopes = scopes, edits = list()))
  if (is.null(scopes)) {
    for (panel in seq_along(panels)) {
      for (index in seq_along(panels[[panel]]$guides$params)) {
        position <- panels[[panel]]$guides$params[[index]]$position
        if (length(position) != 1L || !position %in% c("top", "bottom", "left", "right")) next
        id <- paste(panel, index, sep = ":")
        scopes[[id]] <- list(prefix = paste0(id, "."), panel = panel, index = index, position = position)
      }
    }
  }
  edits <- list()
  for (recipe in recipes) {
    facet <- recipe$facet
    if (length(facet) && !all(names(facet) %in% names(cells))) next
    rows <- if (length(facet)) {
      columns <- cells[names(facet)]
      which(vapply(seq_len(nrow(cells)), function(i) {
        identical(lapply(columns[i, , drop = FALSE], as.character), facet)
      }, logical(1L)))
    } else if (nrow(cells) == 1L) 1L
    if (length(rows) != 1L) next
    panel <- as.integer(cells$PANEL[[rows]])
    matches <- names(Filter(function(scope) {
      identical(as.integer(scope$panel), panel) && identical(scope$position, recipe$position)
    }, scopes))
    if (length(matches) != 1L) next
    id <- matches[[1L]]
    scope <- scopes[[id]]
    members <- ggplot_tick_members(panels[[panel]], scope$index, scope$prefix)
    match <- which(members$major & members$breaks == recipe$key)
    if (length(match) == 1L) edits[[id]][[members$gids[[match]]]] <- list(text = recipe$text)
  }
  list(scopes = scopes, edits = edits)
}

ggplot_resize_drawn_axis_tracks <- function(table, rows, cols) {
  # Each track must still fit its widest or tallest unchanged peer.
  axes <- grepl("^axis-[btlr]-", table$layout$name)
  for (row in unique(rows)) {
    slots <- axes & table$layout$t == row & table$layout$b == row
    table$heights[row] <- do.call(grid::unit.pmax, lapply(table$grobs[slots], grid::grobHeight))
  }
  for (col in unique(cols)) {
    slots <- axes & table$layout$l == col & table$layout$r == col
    table$widths[col] <- do.call(grid::unit.pmax, lapply(table$grobs[slots], grid::grobWidth))
  }
  table
}

ggplot_apply_authored_facet_axes <- function(table, cells, panels, coord, theme, sources) {
  grobs <- table$layout
  indices <- match(paste("panel", cells$COL, cells$ROW, sep = "-"), grobs$name)
  if (anyNA(indices)) return(table)
  plots <- grobs[indices, , drop = FALSE]
  rownames(plots) <- as.character(cells$PANEL)
  rows <- cols <- integer()
  for (scope in sources) {
    side <- scope$position
    marker <- c(top = "t", bottom = "b", left = "l", right = "r")[[side]]
    horizontal <- side %in% c("top", "bottom")
    slots <- which(startsWith(grobs$name, paste0("axis-", marker, "-")))
    draw <- if (horizontal) coord$render_axis_h else coord$render_axis_v
    for (slot in slots) {
      cell <- grobs[slot, ]
      owner <- ggplot_tick_facet_owner(cell, plots, side)
      if (is.null(owner) || as.integer(owner) != scope$panel) next
      table$grobs[[slot]] <- draw(panels[[scope$panel]], theme)[[side]]
      if (horizontal) rows <- c(rows, cell$t) else cols <- c(cols, cell$l)
    }
  }
  ggplot_resize_drawn_axis_tracks(table, rows, cols)
}

ggplot_with_tick_labels <- function(plot, recipes) {
  facet <- plot$facet
  previous <- facet$tavotto_tick_recipes
  if (length(previous)) {
    recipes <- c(previous, recipes)
    facet <- facet$tavotto_tick_base
  }
  force(facet)
  force(recipes)
  plot$facet <- ggplot2::ggproto(NULL, facet, tavotto_tick_base = facet,
    tavotto_tick_recipes = recipes, draw_panels = function(self, panels, layout, x_scales,
                                                           y_scales, ranges, coord, data,
                                                           theme, params) {
      parent <- ggplot2::ggproto_parent(facet, self)
      selection <- ggplot_source_tick_edits(ranges, layout, recipes)
      changed <- ggplot_relabel_tick_members(ranges, selection$scopes, selection$edits)
      single <- inherits(facet, "FacetNull")
      original <- parent$draw_panels(panels, layout, x_scales, y_scales,
                                     if (single) changed$panels else ranges, coord, data, theme, params)
      if (single || !length(selection$edits)) return(original)
      ggplot_apply_authored_facet_axes(original, layout, changed$panels, coord, theme,
                                     selection$scopes[names(selection$edits)])
    })
  plot
}

ggplot_tick_facet_owner <- function(cell, panels, position) {
  across <- if (position %in% c("top", "bottom")) c("l", "r") else c("t", "b")
  same <- panels[[across[[1L]]]] == cell[[across[[1L]]]] &
    panels[[across[[2L]]]] == cell[[across[[2L]]]]
  gap <- switch(position, bottom = cell$t - panels$b, top = panels$t - cell$b,
                left = panels$l - cell$r, right = cell$l - panels$r)
  matching <- which(same & gap > 0L)
  if (!length(matching)) return(NULL)
  nearest <- matching[gap[matching] == min(gap[matching])]
  if (length(nearest) == 1L) rownames(panels)[nearest]
}
