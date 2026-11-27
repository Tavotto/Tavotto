ggplot_tick_member_scopes <- function(built, axes, targets, panels = NULL) {
  # Hash positions once. Changes to paint and tick formatting carry no new identity.
  positions <- lapply(built$data, function(rows) {
    rows[intersect(c("PANEL", "group", "x", "y", "xmin", "xmax", "ymin", "ymax"), names(rows))]
  })
  scene <- rlang::hash(list(positions, built$layout$layout))
  scopes <- list()
  for (gid in names(axes)) {
    axis <- axes[[gid]]
    if (!identical(axis$kind, "axis")) next
    panel <- built$layout$panel_params[[axis$panel]]
    origin <- panel[[axis$axis]]$scale
    context <- rlang::hash(list(scene, class(origin), origin$get_transformation()$name))
    prefix <- paste0(gid, ".label.")
    members <- ggplot_tick_members(panel, axis$index, prefix)
    if (!is.null(members) && identical(as.list(members$labels), targets[[gid]]$tick_labels)) {
      owner <- built$layout$layout
      row <- which(as.integer(owner$PANEL) == axis$panel)
      facet <- if (length(row) == 1L && length(panels$facets)) {
        lapply(owner[row, panels$facets, drop = FALSE], as.character)
      }
      scopes[[gid]] <- list(prefix = prefix, context = context, panel = axis$panel,
                           index = axis$index, position = axis$position, facet = facet,
                           source_members = if (!is.null(panels)) members$gids[members$major])
    }
  }
  scopes
}

ggplot_tick_member_edits <- function(scopes, edits) {
  result <- list()
  for (gid in names(scopes)) {
    prefix <- scopes[[gid]]$prefix
    members <- edits[startsWith(names(edits) %||% character(), prefix)]
    if (!length(members)) next
    valid <- vapply(members, function(edit) {
      identical(names(edit), "text") && is.character(edit$text) &&
        length(edit$text) == 1L && !is.na(edit$text)
    }, logical(1L))
    if (!all(valid) || any(!grepl("^[a-f0-9]{32}$", substring(names(members), nchar(prefix) + 1L)))) {
      stop("Invalid native R tick label edit.")
    }
    result[[gid]] <- members
  }
  result
}

ggplot_single_text_grob <- function(text, index) {
  pick <- function(value) if (length(value)) value[(index - 1L) %% length(value) + 1L]
  grid::textGrob(pick(text$label), x = pick(text$x), y = pick(text$y), just = text$just,
                 hjust = pick(text$hjust), vjust = pick(text$vjust), rot = pick(text$rot),
                 gp = do.call(grid::gpar, lapply(text$gp, pick)))
}

ggplot_measure_tick_axis <- function(drawn, layout, group, scope, gid, frame_pt, originals) {
  on.exit(grid::upViewport(0))
  members <- ggplot_tick_members(layout$panel_params[[scope$panel]], scope$index, scope$prefix)
  if (is.null(members) || is.null(group)) return(list())
  child <- if (!isTRUE(group$hidden)) ggplot_drawn_text(drawn, group)
  matches <- !is.null(child) && identical(as.character(child$label), members$labels)
  result <- list()
  for (i in which(members$major)) {
    id <- members$gids[[i]]
    value <- members$labels[[i]]
    if (!matches && is.null(originals[[id]])) next
    if (!nzchar(value) && is.null(originals[[id]])) next
    bounds <- c(-1, -1, 0, 0)
    if (matches && nzchar(value)) {
      bounds <- ggplot_text_grob_bounds(ggplot_single_text_grob(child, i), frame_pt)
      if (!all(is.finite(bounds)) || any(bounds[3:4] <= 0)) next
    }
    field <- list(prop = "text", type = "text", value = value, value_original = originals[[id]])
    source <- if (id %in% scope$source_members && !is.null(originals[[id]])) {
      list(position = scope$position, facet = scope$facet, key = members$breaks[[i]], text = value)
    }
    result[[id]] <- list(gid = id, role = "ticklabel", parent_gid = gid, source_tick_label = source,
                         tick_member_context = scope$context,
                         label = value, bbox = as.list(bounds), draggable = FALSE,
                         editable = list(field))
  }
  result
}

ggplot_measure_tick_members <- function(layout, targets, scopes, frame_pt, originals = NULL) {
  if (!length(scopes)) return(list())
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  axes <- lapply(names(scopes), function(gid) {
    tryCatch(
      ggplot_measure_tick_axis(drawn, layout, targets[[gid]], scopes[[gid]], gid, frame_pt, originals),
      error = function(error) list()
    )
  })
  do.call(c, axes)
}
