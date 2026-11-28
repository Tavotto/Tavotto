ggplot_ribbon_leaves <- function(grob) {
  if (!is.null(grob$vp)) return(NULL)
  if (inherits(grob, "zeroGrob")) return(list())
  if (inherits(grob, "polygon") || inherits(grob, "polyline")) return(list(grob))
  if (!identical(class(grob)[[1L]], "gTree")) return(NULL)
  children <- lapply(grob$children, ggplot_ribbon_leaves)
  if (any(vapply(children, is.null, logical(1L)))) return(NULL)
  unlist(children, recursive = FALSE, use.names = FALSE)
}

# Validate every group's native projected polygon before associating its
# enclosing child with the captured fill category. A paint match is ambiguous.
ggplot_ribbon_members <- function(draw, layout, groups) {
  rows <- draw$rows
  membership <- groups$membership[[as.character(draw$panel_id)]]
  if (length(membership) != nrow(rows) || anyNA(membership) ||
      !is.logical(rows$flipped_aes) || anyNA(rows$flipped_aes) ||
      length(unique(rows$flipped_aes)) != 1L) return(list())
  along <- if (isTRUE(rows$flipped_aes[[1L]])) "y" else "x"
  across <- setdiff(c("x", "y"), along)
  ends <- paste0(across, c("max", "min"))
  layer_groups <- split(seq_len(nrow(rows)), factor(rows$group))
  children <- draw$grob$children
  if (length(children) != length(layer_groups)) return(list())
  categories <- vapply(seq_along(layer_groups), function(i) {
    indices <- layer_groups[[i]]
    colours <- unique(membership[indices])
    if (length(colours) != 1L || length(indices) < 2L) return(NA_integer_)
    selected <- rows[indices, c(along, ends), drop = FALSE]
    if (!all(vapply(selected, function(values) all(is.finite(values)), logical(1L)))) return(NA_integer_)
    upper <- rep(TRUE, nrow(selected))
    if ("align_padding" %in% names(rows)) {
      padding <- rows$align_padding[indices]
      if (!is.logical(padding)) return(NA_integer_)
      # StatAlign's padding belongs to the lower edge of the native polygon.
      upper <- is.na(padding) | !padding
    }
    expected <- data.frame(first = c(selected[[along]][upper], rev(selected[[along]])),
                           second = c(selected[[ends[[1L]]]][upper], rev(selected[[ends[[2L]]]])))
    names(expected) <- c(along, across)
    projected <- layout$coord$transform(expected, layout$panel_params[[draw$panel_id]])
    polygons <- Filter(function(g) inherits(g, "polygon"), ggplot_ribbon_leaves(children[[i]]))
    if (length(polygons) != 1L || !all(vapply(c("x", "y"), function(axis) {
      isTRUE(all.equal(as.numeric(polygons[[1L]][[axis]]), projected[[axis]]))
    }, logical(1L)))) return(NA_integer_)
    colours[[1L]]
  }, integer(1L))
  if (anyNA(categories)) return(list())
  split(seq_along(categories), categories)
}

ggplot_fill_ribbon <- function(grob, pigment) {
  if (inherits(grob, "polygon")) {
    grob$gp$fill <- pigment
  } else {
    for (i in seq_along(grob$children)) grob$children[[i]] <- ggplot_fill_ribbon(grob$children[[i]], pigment)
  }
  grob
}

# An explicitly hidden fill keeps its authored colour and opacity for the next edit.
ggplot_ribbon_pigment <- function(polygon) {
  face <- ggplot_uniform_pigment(polygon$gp$fill)
  if (isTRUE(face$alpha == 0)) {
    if (is.null(polygon$tavotto_ribbon_rgb)) return(NULL)
    face$rgb <- polygon$tavotto_ribbon_rgb
    if (!is.null(polygon$tavotto_ribbon_alpha)) face$alpha <- polygon$tavotto_ribbon_alpha
  }
  face
}

ggplot_record_ribbon_paint <- function(grob, values) {
  if (inherits(grob, "polygon")) {
    face <- ggplot_ribbon_pigment(grob)
    if (is.null(face)) stop("Tavotto could not match the R ribbon fill.")
    colour <- if (is.null(values$facecolor)) face$rgb else values$facecolor
    grob$tavotto_ribbon_rgb <- colour
    grob$tavotto_ribbon_alpha <- if (colour == "none") {
      if (is.null(values$alpha)) face$alpha else values$alpha
    }
    grob$gp$fill <- ggplot_edit_pigment(colour, face, values$alpha, face$alpha)
  } else {
    for (i in seq_along(grob$children)) {
      grob$children[[i]] <- ggplot_record_ribbon_paint(grob$children[[i]], values)
    }
  }
  grob
}

ggplot_apply_fixed_ribbon_paint <- function(grob, rows, layout, panel, values) {
  ggplot_record_ribbon_paint(grob, values)
}

ggplot_apply_ribbon_styles <- function(grob, rows, layout, panel, values) {
  group <- factor(rows$group)
  ids <- as.character(panel)
  groups <- list(membership = stats::setNames(list(as.integer(group)), ids))
  children <- ggplot_ribbon_members(list(rows = rows, grob = grob, panel_id = panel), layout, groups)
  if (length(children) != nlevels(group)) stop("Tavotto could not match the R ribbon categories.")
  changes <- split(values$alpha, group)
  for (id in names(children)) {
    alpha <- unique(changes[[as.integer(id)]])
    if (length(alpha) != 1L) stop("Tavotto found conflicting R ribbon styles.")
    if (is.na(alpha)) next
    for (i in children[[id]]) {
      grob$children[[i]] <- ggplot_record_ribbon_paint(grob$children[[i]], list(alpha = alpha))
    }
  }
  grob
}
