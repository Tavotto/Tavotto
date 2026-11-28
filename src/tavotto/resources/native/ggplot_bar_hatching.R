ggplot_bar_hatch_props <- c("hatch", "hatchcolor", "hatch_linewidth")

ggplot_hatch_tile <- function(spec, background, ink, linewidth, tile_pt) {
  children <- list(grid::rectGrob(gp = grid::gpar(fill = background, col = NA_character_)))
  paint <- grid::gpar(col = if (linewidth > 0) ink else NA_character_, fill = ink,
                      lwd = linewidth / ggplot_lwd_pt, lineend = "butt", linejoin = "mitre")
  if (length(spec$open)) {
    coordinates <- matrix(unlist(spec$open), ncol = 4L, byrow = TRUE)
    children[[length(children) + 1L]] <- grid::segmentsGrob(
      x0 = coordinates[, 1L], y0 = coordinates[, 2L],
      x1 = coordinates[, 3L], y1 = coordinates[, 4L], gp = paint
    )
  }
  if (length(spec$closed)) {
    coordinates <- matrix(unlist(spec$closed), ncol = 2L, byrow = TRUE)
    children[[length(children) + 1L]] <- grid::pathGrob(
      x = coordinates[, 1L], y = coordinates[, 2L], rule = "winding",
      id.lengths = lengths(spec$closed), gp = paint
    )
  }
  size <- grid::unit(tile_pt * 72.27 / 72, "pt")
  grid::pattern(grid::grobTree(children = do.call(grid::gList, children)),
                x = 0, y = 0, width = size, height = size, extend = "repeat",
                just = c("left", "bottom"))
}

ggplot_bar_native_rect <- function(grob) {
  attr(grob, "tavotto_hatch_rect", exact = TRUE) %||% grob
}

ggplot_bar_hatch_edge <- function(rect) {
  edge <- rect$gp$col[[1L]]
  if (is.na(edge)) return("#000000")
  substr(scales::alpha(edge, 1), 1L, 7L)
}

ggplot_bar_hatch_defaults <- function(rows) {
  alpha <- rows$alpha
  if (!is.numeric(alpha) || !length(alpha)) alpha <- rep(1, nrow(rows))
  alpha[is.na(alpha)] <- 1
  list(hatch = rep("", nrow(rows)), hatchcolor = rep("edge", nrow(rows)),
       hatch_linewidth = rep(1, nrow(rows)), opacity = alpha)
}

ggplot_bar_hatch_fields <- function(rect, selected, rows) {
  state <- attr(rect, "tavotto_bar_hatching", exact = TRUE) %||% ggplot_bar_hatch_defaults(rows)
  values <- lapply(state[ggplot_bar_hatch_props], function(field) {
    distinct <- unique(field[selected])
    if (length(distinct) == 1L) distinct
  })
  Filter(Negate(is.null), values)
}

ggplot_record_bar_hatch <- function(rect, rows, values, source, catalog) {
  state <- attr(rect, "tavotto_bar_hatching", exact = TRUE) %||% ggplot_bar_hatch_defaults(source)
  for (prop in intersect(names(values), ggplot_bar_hatch_props)) {
    field <- rep_len(values[[prop]], length(rows))
    selected <- !is.na(field)
    state[[prop]][rows[selected]] <- field[selected]
  }
  if (!is.null(values$alpha)) {
    field <- rep_len(values$alpha, length(rows))
    selected <- !is.na(field)
    state$opacity[rows[selected]] <- field[selected]
  }
  state$patterns <- utils::modifyList(state$patterns %||% list(), catalog$patterns %||% list())
  state$tile_pt <- catalog$tile_pt %||% state$tile_pt
  attr(rect, "tavotto_bar_hatching") <- state
  rect
}

ggplot_draw_bar_hatching <- function(rect) {
  state <- attr(rect, "tavotto_bar_hatching", exact = TRUE)
  if (is.null(state) || !any(nzchar(state$hatch))) return(rect)
  count <- length(rect$x)
  fill <- rep_len(rect$gp$fill, count)
  edge <- rep_len(rect$gp$col, count)
  color <- state$hatchcolor
  follows <- color == "edge"
  color[follows] <- ifelse(is.na(edge[follows]), "#000000", edge[follows])
  ink <- scales::alpha(color, state$opacity)
  keys <- ifelse(nzchar(state$hatch), paste(state$hatch, fill, ink, state$hatch_linewidth, sep = ":"), "")
  runs <- rle(keys)$lengths
  batches <- split(seq_len(count), rep(seq_along(runs), runs))
  stamps <- new.env(parent = emptyenv())
  children <- lapply(batches, function(rows) {
    first <- rows[[1L]]
    piece <- rect
    for (field in c("x", "y", "width", "height")) piece[[field]] <- rect[[field]][rows]
    piece$gp <- structure(lapply(rect$gp, function(field) rep_len(field, count)[rows]), class = "gpar")
    symbol <- state$hatch[[first]]
    if (nzchar(symbol)) {
      key <- keys[[first]]
      if (is.null(stamps[[key]])) {
        spec <- state$patterns[[symbol]]
        if (is.null(spec)) stop("Tavotto could not resolve the native R bar pattern.")
        stamps[[key]] <- ggplot_hatch_tile(
          spec, fill[[first]], ink[[first]], state$hatch_linewidth[[first]], state$tile_pt
        )
      }
      piece$gp <- do.call(grid::gpar, c(
        unclass(piece$gp)[setdiff(names(piece$gp), "fill")],
        list(fill = stamps[[key]])
      ))
    }
    piece$name <- paste0("bar-", first)
    attr(piece, "tavotto_bar_hatching") <- NULL
    piece
  })
  group <- grid::grobTree(children = do.call(grid::gList, children), name = rect$name)
  attr(group, "tavotto_hatch_rect") <- rect
  group
}
