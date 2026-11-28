ggplot_text_member <- function(values, index) {
  if (length(values) > 1L) values[(index - 1L) %% length(values) + 1L] else values
}

ggplot_split_text_layer <- function(draw) {
  text <- draw$grob
  labels <- as.character(text$label)
  members <- lapply(seq_along(labels), function(index) {
    piece <- text
    for (field in c("x", "y", "hjust", "vjust", "rot", "label")) {
      piece[[field]] <- ggplot_text_member(text[[field]], index)
    }
    piece$label <- as.character(piece$label)
    piece$gp <- structure(lapply(text$gp, ggplot_text_member, index = index), class = "gpar")
    piece$name <- paste0("text-", index)
    grid::grobTree(piece, name = paste0("label-", index))
  })
  grid::grobTree(children = do.call(grid::gList, members), name = text$name)
}

# Use the draw-time anchor and gpar of each row. Leave each native text layer
# in one original panel slot so a later layer order edit still addresses it.
ggplot_data_text_scene <- function(table, layout, panels, grobs, data, layers) {
  targets <- list()
  if (is.null(panels) || !length(grobs)) return(list(table = table, targets = targets))
  families <- NULL
  for (index in seq_along(layers)) {
    layer <- layers[[index]]
    boxed <- ggplot_stock_geom(layer$geom, "GeomLabel")
    if (!boxed && !ggplot_stock_geom(layer$geom, "GeomText")) next
    for (draw in ggplot_panel_draws(panels, grobs[[index]], data[[index]])) {
      actual <- ggplot_drawn_data_labels(draw$grob, boxed)
      baseline <- draw$grob$tavotto_data_label_baseline %||% actual
      if (!length(actual) || anyNA(actual) || !identical(baseline, as.character(draw$rows$label))) next
      replacement <- if (boxed) draw$grob else ggplot_split_text_layer(draw)
      table$grobs[[draw$slot]]$children[[draw$child]] <- replacement
      if (is.null(families)) families <- ggplot_installed_text_fonts()
      panel_id <- if (panels$single) "" else paste0(".panel.", draw$panel_id)
      positions <- ggplot_data_label_rows(draw$rows, layout, draw$panel_id)
      for (row in seq_along(replacement$children)) {
        slots <- list(draw$slot, draw$grob$name, replacement$children[[row]]$name)
        allow_empty <- boxed || !is.null(draw$grob$tavotto_data_label_baseline)
        title_child <- if (boxed) replacement$children[[row]]$children[[2L]]$name
        target <- ggplot_text_target(table, slots, "text", title_child, allow_empty = allow_empty)
        if (is.null(target)) next
        gid <- paste0("layer.", index, panel_id, ".text.", row)
        target$data_label <- TRUE
        target$member <- row
        target$hidden <- !boxed && !nzchar(target$label)
        if (boxed) {
          target$boxed <- list(
            rows = draw$rows[row, , drop = FALSE], geom = layer$geom,
            params = layer$computed_geom_params %||% layer$geom_params,
            panel = layout$panel_params[[draw$panel_id]], coord = layout$coord
          )
        }
        if (!is.null(positions)) {
          owner <- as.list(positions[row, , drop = FALSE])
          if (identical(ggplot_data_label_row(positions, owner), row)) {
            kind <- if (boxed) "GeomLabel" else "GeomText"
            target$source <- list(layer = index, facet = draw$facet, owner = owner, kind = kind)
          }
        }
        target$clip <- isTRUE(table$grobs[[draw$slot]]$vp$clip)
        key <- target$source$owner
        if (is.null(key)) {
          key <- list(row, draw$rows[row, , drop = FALSE])
        } else {
          key$x <- signif(key$x, 14L)
          key$y <- signif(key$y, 14L)
        }
        target$data_label_context <- rlang::hash(list(index, draw$facet, key))
        target$font_families <- families
        target$font_size_editable <- is_positive_ggplot_number(target$paint$fontsize) &&
          is_positive_ggplot_number(ggplot_text_cex(target$paint))
        targets[[gid]] <- target
      }
    }
  }
  list(table = table, targets = targets)
}

ggplot_measure_data_labels <- function(table, drawn, targets, frame_pt) {
  groups <- split(names(targets), vapply(targets, function(target) {
    paste(target$slots[[1L]], target$slots[[2L]], sep = ":")
  }, character(1L)))
  measured <- list()
  for (group in groups) {
    first <- targets[[group[[1L]]]]
    slot <- first$slots[[1L]]
    layer <- first$slots[[2L]]
    viewport <- ggplot_drawn_panel_viewport(drawn, table, slot, layer)
    if (is.null(viewport)) next
    text_layer <- table$grobs[[slot]]$children[[layer]]
    grid::downViewport(viewport, strict = TRUE, recording = FALSE)
    tryCatch({
      clip <- if (first$clip) unlist(ggplot_panel_clip(frame_pt))
      for (gid in group) {
        target <- targets[[gid]]
        bounds <- if (!isTRUE(target$hidden)) {
          member <- text_layer$children[[target$member]]
          box <- if (is.null(target$boxed)) {
            ggplot_text_grob_bounds(member$children[[1L]], frame_pt)
          } else ggplot_boxed_label_bounds(member, frame_pt)
          if (!is.null(clip)) {
            lo <- pmax(box[1:2], clip[1:2])
            hi <- pmin(box[1:2] + box[3:4], clip[1:2] + clip[3:4])
            box <- c(lo, hi - lo)
          }
          if (all(is.finite(box)) && all(box[3:4] > 0)) box
        }
        target$bbox <- bounds %||% c(-1, -1, 0, 0)
        measured[[gid]] <- target
      }
    }, finally = grid::upViewport(0, recording = FALSE))
  }
  measured
}

ggplot_boxed_label_bounds <- function(member, frame_pt) {
  grid::pushViewport(member$vp, recording = FALSE)
  on.exit(grid::upViewport(recording = FALSE))
  border <- member$children[[1L]]
  left <- grid::grobX(border, 180)
  right <- grid::grobX(border, 0)
  bottom <- grid::grobY(border, 270)
  top <- grid::grobY(border, 90)
  corners <- grid::deviceLoc(
    grid::unit.c(left, left, right, right), grid::unit.c(bottom, top, bottom, top),
    valueOnly = TRUE
  )
  ggplot_bbox(list(x = min(corners$x), y = min(corners$y)),
              list(x = max(corners$x), y = max(corners$y)), frame_pt)
}

ggplot_edit_boxed_label <- function(table, target, paint, label) {
  slot <- target$slots[[1L]]
  layer <- target$slots[[2L]]
  index <- target$member
  grob <- table$grobs[[slot]]$children[[layer]]$children[[index]]
  box <- target$boxed
  table$grobs[[slot]]$children[[layer]]$children[[index]] <- ggplot_draw_boxed_label(
    grob, box$rows, box$geom, box$params, box$panel, box$coord,
    list(paint = paint, text = label)
  )
  table
}
