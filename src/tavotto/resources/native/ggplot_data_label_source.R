ggplot_data_label_rows <- function(rows, layout, panel_id) {
  panel <- layout$panel_params[[panel_id]]
  positions <- lapply(c("x", "y"), function(axis) {
    native <- rows[[axis]]
    if (!is.numeric(native)) return(NULL)
    scale <- panel[[axis]]$scale
    transform <- scale$get_transformation
    if (is.function(transform)) native <- transform()$inverse(native)
    as.numeric(native)
  })
  if (any(lengths(positions) != nrow(rows))) return(NULL)
  data.frame(label = as.character(rows$label), x = positions[[1L]], y = positions[[2L]])
}

ggplot_data_label_row <- function(rows, owner) {
  if (is.null(rows) || !all(is.finite(c(owner$x, owner$y)))) return(NULL)
  x <- abs(rows$x - owner$x) <= 1e-12 * max(1, abs(owner$x))
  y <- abs(rows$y - owner$y) <= 1e-12 * max(1, abs(owner$y))
  index <- which(rows$label == owner$label & x & y)
  if (length(index) == 1L) index
}

ggplot_boxed_label_text <- function(member) {
  children <- member$children
  if (!inherits(member$vp, "viewport") || length(children) != 2L ||
      !inherits(children[[1L]], "roundrect") || !inherits(children[[2L]], "titleGrob")) return(NULL)
  text <- children[[2L]]$children
  if (length(text) == 1L && inherits(text[[1L]], "text") &&
      is.character(text[[1L]]$label) && length(text[[1L]]$label) == 1L) text[[1L]]
}

ggplot_draw_boxed_label <- function(member, rows, geom, params, panel, coord, values) {
  text <- ggplot_boxed_label_text(member)
  if (is.null(text)) stop("Tavotto could not match the native R boxed label.")
  params <- params[intersect(names(params), geom$parameters())]
  # Preserve the native fill, border, padding and rounding. The edited font
  # size is already in device points, irrespective of the plot's size.unit.
  params$text.colour <- values$paint$col %||% text$gp$col
  params$size.unit <- "pt"
  rows$size <- values$paint$fontsize %||% text$gp$fontsize
  rows$fontface <- values$paint$font %||% text$gp$font
  rows$family <- values$paint$fontfamily %||% text$gp$fontfamily
  rows$label <- values$text %||% text$label
  rows$angle <- values$rotation %||% member$vp$angle
  rendered <- do.call(geom$draw_panel, c(list(rows, panel, coord), params))
  replacement <- rendered$children[[1L]]
  replacement$name <- member$name
  replacement
}

ggplot_drawn_data_labels <- function(grob, boxed) {
  if (boxed) {
    if (!length(grob$children)) return(NULL)
    labels <- lapply(grob$children, function(member) ggplot_boxed_label_text(member)$label)
    if (any(lengths(labels) != 1L)) return(NULL)
    return(unname(unlist(labels)))
  }
  if (!inherits(grob, "text") || !is.null(grob$vp) || isTRUE(grob$check.overlap) ||
      !(is.character(grob$label) || is.numeric(grob$label))) return(NULL)
  as.character(grob$label)
}

ggplot_write_data_label <- function(grob, rows, layout, panel_id, values, layer, boxed) {
  labels <- ggplot_drawn_data_labels(grob, boxed)
  original <- grob$tavotto_data_label_baseline %||% labels
  if (is.null(labels) || !identical(original, as.character(rows$label))) {
    stop("Tavotto could not match the native R data labels.")
  }
  locations <- ggplot_data_label_rows(rows, layout, panel_id)
  for (recipe in values) {
    index <- ggplot_data_label_row(locations, recipe$owner)
    if (is.null(index)) stop("Tavotto could not find the selected native R data label.")
    if (boxed) {
      grob$children[[index]] <- ggplot_draw_boxed_label(
        grob$children[[index]], rows[index, , drop = FALSE], layer$geom, layer$geom_params,
        layout$panel_params[[panel_id]], layout$coord, recipe
      )
    } else {
      for (prop in names(recipe$paint)) {
        field <- rep_len(grob$gp[[prop]], length(original))
        field[[index]] <- recipe$paint[[prop]]
        grob$gp[[prop]] <- field
      }
      if (!is.null(recipe$text)) grob$label[[index]] <- recipe$text
      if (!is.null(recipe$rotation)) {
        grob$rot <- rep_len(grob$rot, length(original))
        grob$rot[[index]] <- recipe$rotation
      }
    }
  }
  grob$tavotto_data_label_baseline <- original
  grob
}

ggplot_with_data_labels <- function(layer, recipes, kind = "GeomText") {
  if (!kind %in% c("GeomText", "GeomLabel") || !identical(class(layer$geom)[[1L]], kind)) {
    stop("Tavotto label layer changed.")
  }
  boxed <- identical(kind, "GeomLabel")
  force(layer)
  ggplot_set_layer_geometry(layer, recipes, function(grob, rows, layout, panel_id, values) {
    ggplot_write_data_label(grob, rows, layout, panel_id, values, layer, boxed)
  })
}
