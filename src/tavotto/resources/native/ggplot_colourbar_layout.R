ggplot_colourbar_directions <- c("vertical", "horizontal")
ggplot_colourbar_extends <- c("neither", "both", "min", "max")
ggplot_colourbar_tip_prefix <- "tavotto-colourbar-end-"
ggplot_colourbar_tip_fraction <- .05
ggplot_colourbar_outline_keys <- c("outline_visible", "outline_width")

ggplot_colourbar_endpoints <- function(params, inverse) {
  values <- params$decor$value
  colours <- params$decor$colour
  if (!is.numeric(values) || length(values) < 2L || any(!is.finite(values)) ||
      !is.character(colours) || length(colours) != length(values) || anyNA(colours)) return(NULL)
  # Guide decorations use transformed units. A descending scale can reverse
  # the data endpoints even when the guide's reverse flag is false.
  native <- tryCatch(suppressWarnings(inverse(values[c(1L, length(values))])),
                     error = function(error) NULL)
  if (!is.numeric(native) || length(native) != 2L || any(!is.finite(native)) ||
      native[[1L]] == native[[2L]]) return(NULL)
  stats::setNames(c(1L, length(values))[order(native)], c("min", "max"))
}

ggplot_stock_colourbar_kind <- function(guide) {
  kind <- class(guide)[[1L]]
  if (kind %in% c("GuideColourbar", "GuideColoursteps")) kind
}

ggplot_binned_colourbar_edges <- function(params, scale) {
  if (!inherits(scale, "ScaleBinned")) return(NULL)
  decor <- params$decor
  centres <- (decor$min + decor$max) / 2
  if (length(centres) < 2L || any(!is.finite(centres)) || anyDuplicated(centres) ||
      !is.character(decor$colour) || anyNA(decor$colour)) return(NULL)
  positions <- order(centres)
  edge <- c(1L, length(centres))
  values <- scale$get_transformation()$inverse(sort(scale$get_limits()))
  if (length(values) != 2L || any(!is.finite(values)) || values[[1L]] == values[[2L]]) return(NULL)
  # The guide's reversed coordinates and the scale's transformed direction
  # are independent. Find the actual native colour at each displayed end.
  values <- values[if (positions[[1L]] == 1L) c(1L, 2L) else c(2L, 1L)]
  list(colours = decor$colour[positions],
       endpoints = stats::setNames(edge[order(values)], c("min", "max")))
}

ggplot_extend_colourbar <- function(table, params, extend, endpoints) {
  slot <- which(table$layout$name == "bar")
  if (length(slot) != 1L || is.null(endpoints)) stop("The native R colourbar has no drawable endpoints.")
  horizontal <- identical(params$direction, "horizontal")
  for (side in intersect(c("min", "max"), if (extend == "both") c("min", "max") else extend)) {
    # Re-read the bar cell after inserting either end. The gtable grows around
    # the native strip, whose length and data-facing tick positions stay intact.
    bar <- table$layout[slot, ]
    endpoint <- endpoints[[side]]
    tip <- as.integer(endpoint != 1L)
    paint <- grid::gpar(fill = params$decor$colour[[endpoint]], col = NA)
    name <- paste0(ggplot_colourbar_tip_prefix, side)
    if (horizontal) {
      index <- if (tip == 0L) bar$l else bar$r + 1L
      length <- sum(table$widths[bar$l:bar$r]) * ggplot_colourbar_tip_fraction
      table <- gtable::gtable_add_cols(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(1 - tip, 1 - tip, tip), y = c(0, 1, .5), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = bar$t, l = index, b = bar$b, name = name)
    } else {
      index <- if (tip == 1L) bar$t else bar$b + 1L
      length <- sum(table$heights[bar$t:bar$b]) * ggplot_colourbar_tip_fraction
      table <- gtable::gtable_add_rows(table, length, pos = index - 1L)
      shape <- grid::polygonGrob(x = c(0, 1, .5), y = c(1 - tip, 1 - tip, tip), gp = paint)
      table <- gtable::gtable_add_grob(table, shape, t = index, l = bar$l, r = bar$r, name = name)
    }
  }
  table
}

ggplot_colourbar_frame <- function(frame) {
  if (inherits(frame, "zeroGrob")) {
    width <- ggplot2::theme_grey()$rect$linewidth * ggplot2::.pt * ggplot_lwd_pt
    return(list(outline_visible = FALSE, outline_width = width))
  }
  if (inherits(frame, "rect") && is.numeric(frame$gp$lwd) && length(frame$gp$lwd) == 1L &&
      is.finite(frame$gp$lwd) && frame$gp$lwd >= 0) {
    list(outline_visible = TRUE, outline_width = frame$gp$lwd * ggplot_lwd_pt)
  }
}

ggplot_draw_colourbar_frame <- function(table, slot, original, edit, horizontal) {
  visible <- if (is.null(edit$outline_visible)) original$outline_visible else edit$outline_visible
  if (!visible) {
    table$grobs[[slot]] <- ggplot2::zeroGrob()
    return(table)
  }
  width <- if (is.null(edit$outline_width)) original$outline_width else edit$outline_width
  grob <- table$grobs[[slot]]
  if (inherits(grob, "zeroGrob")) {
    grob <- grid::rectGrob(gp = grid::gpar(col = ggplot2::theme_grey()$rect$colour, fill = NA))
  }
  grob$gp$lwd <- width / ggplot_lwd_pt
  tips <- table$layout[startsWith(table$layout$name, ggplot_colourbar_tip_prefix), ]
  if (nrow(tips)) {
    bar <- table$layout[table$layout$name == "bar", ]
    start <- if (horizontal) any(tips$r < bar$l) else any(tips$t > bar$b)
    end <- if (horizontal) any(tips$l > bar$r) else any(tips$b < bar$t)
    fraction <- ggplot_colourbar_tip_fraction
    a <- start * fraction
    b <- a + 1
    along <- c(if (start) 0, a, b, if (end) b + fraction, b, a) / (b + end * fraction)
    across <- c(if (start) .5, 0, 0, if (end) .5, 1, 1)
    grob <- if (horizontal) grid::polygonGrob(x = along, y = across, gp = grob$gp) else
      grid::polygonGrob(x = across, y = along, gp = grob$gp)
    table$layout[slot, c("t", "l", "b", "r")] <- list(
      min(c(bar$t, tips$t)), min(c(bar$l, tips$l)), max(c(bar$b, tips$b)), max(c(bar$r, tips$r))
    )
    # Put one stroke above the extension fills, without drawing seams across
    # the junctions or changing the data strip and its tick positions.
    table$layout$z[slot] <- max(table$layout$z) + 1
  }
  table$grobs[[slot]] <- grob
  table
}

ggplot_capture_colourbar <- function(guide, key, scale, extend, outline) {
  force(guide)
  force(key)
  force(scale)
  force(extend)
  force(outline)
  ggplot_clone(guide, tavotto_colourbar_base = guide, tavotto_colourbar_extend = extend,
    assemble_drawing = function(self, grobs, layout, sizes, params, elems) {
    table <- guide$assemble_drawing(grobs, layout, sizes, params, elems)
    steps <- if (identical(ggplot_stock_colourbar_kind(guide), "GuideColoursteps")) {
      ggplot_binned_colourbar_edges(params, scale)
    }
    endpoints <- if (is.null(steps)) {
      ggplot_colourbar_endpoints(params, scale$get_transformation()$inverse)
    } else {
      params$decor$colour <- steps$colours
      steps$endpoints
    }
    frame <- which(table$layout$name == "frame")
    original <- if (length(frame) == 1L) ggplot_colourbar_frame(table$grobs[[frame]])
    tips <- !is.null(extend) && extend != "neither"
    if (tips) table <- ggplot_extend_colourbar(table, params, extend, endpoints)
    if (!is.null(original) && (length(outline) || (tips && original$outline_visible))) {
      table <- ggplot_draw_colourbar_frame(table, frame, original, outline, params$direction == "horizontal")
    }
    attr(table, "tavotto_colourbar") <- list(
      index = key, orientation = params$direction, extend = extend, can_extend = !is.null(endpoints),
      reversed = if (is.null(endpoints)) isTRUE(params$reverse) else endpoints[["min"]] != 1L,
      outline = original
    )
    table
  })
}

ggplot_colourbar_scale <- function(scales, aesthetics) {
  if (!length(aesthetics) || !all(aesthetics %in% c("colour", "fill"))) return(NULL)
  scale <- scales$get_scales(aesthetics[[1L]])
  if (all(vapply(aesthetics, function(channel) {
    identical(scale, scales$get_scales(channel))
  }, logical(1L)))) scale
}

ggplot_colourbar_guides <- function(guides, scales, extensions = NULL, outlines = NULL) {
  actual <- guides$guides
  for (key in names(actual)) {
    guide <- actual[[key]]
    extend <- extensions[[key]]
    if (!is.null(guide$tavotto_colourbar_base)) {
      if (is.null(extend)) extend <- guide$tavotto_colourbar_extend
      guide <- guide$tavotto_colourbar_base
    }
    if (!is.null(ggplot_stock_colourbar_kind(guide)) &&
        identical(as.list(guide)$assemble_drawing, as.list(ggplot2::GuideColourbar)$assemble_drawing)) {
      aesthetic <- guides$params[[key]]$aesthetic
      scale <- ggplot_colourbar_scale(scales, aesthetic)
      if (is.null(scale)) next
      actual[[key]] <- ggplot_capture_colourbar(
        guide, key, scale, extend, outlines[[key]]
      )
    }
  }
  ggplot_clone(guides, guides = actual)
}

ggplot_colourbar_source_scope <- function(guide, params) {
  kind <- ggplot_stock_colourbar_kind(guide)
  if (is.null(kind) || !length(params$aesthetic) ||
      !all(params$aesthetic %in% c("colour", "fill"))) return(NULL)
  title <- params$title
  if (!is.null(title) && (!is.character(title) || length(title) != 1L || is.na(title))) return(NULL)
  list(aesthetics = as.list(sort(params$aesthetic)), position = params$position, title = title,
       kind = kind)
}

ggplot_with_colourbar_layout <- function(plot, recipes) {
  original <- plot$guides
  force(recipes)
  plot$guides <- ggplot2::ggproto(NULL, original, build = function(self, scales, ...) {
    built <- ggplot2::ggproto_parent(original, self)$build(scales, ...)
    params <- built$params
    extensions <- list()
    for (recipe in recipes) {
      slots <- names(params)[vapply(seq_along(params), function(index) {
        identical(ggplot_colourbar_source_scope(built$guides[[index]], params[[index]]), recipe$scope)
      }, logical(1L))]
      if (length(slots) != 1L) next
      slot <- slots[[1L]]
      if (!is.null(recipe$orientation)) params[[slot]]$direction <- recipe$orientation
      if (!is.null(recipe$extend)) extensions[[slot]] <- recipe$extend
    }
    ggplot_colourbar_guides(ggplot_clone(built, params = params), scales, extensions)
  })
  plot
}
