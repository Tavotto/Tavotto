ggplot_errorbar_caps <- function(line, flipped) {
  line <- if (is.null(line$tavotto_cap_base)) line else line$tavotto_cap_base
  runs <- rle(line$id)
  if (length(unique(flipped)) != 1L || !is.logical(flipped) || anyNA(flipped) ||
      any(runs$lengths != 8L) || anyDuplicated(runs$values)) return(NULL)
  points <- matrix(seq_along(line$id), nrow = 8L)
  horizontal <- isTRUE(flipped[[1L]])
  along <- as.numeric(if (horizontal) line$y else line$x)
  across <- as.numeric(if (horizontal) line$x else line$y)
  ends <- points[c(1L, 2L, 4L, 5L, 7L, 8L), , drop = FALSE]
  separators <- points[c(3L, 6L), , drop = FALSE]
  if (!all(is.finite(along[ends]) & is.finite(across[ends])) ||
      !all(is.na(along[separators]) & is.na(across[separators])) ||
      !all(along[points[4L, ]] == along[points[5L, ]]) ||
      !all(across[points[1L, ]] == across[points[4L, ]] &
           across[points[2L, ]] == across[points[4L, ]] &
           across[points[7L, ]] == across[points[5L, ]] &
           across[points[8L, ]] == across[points[5L, ]])) return(NULL)
  list(points = points, axis = if (horizontal) "y" else "x")
}

ggplot_cap_span <- function(line, caps, row = 1L) {
  if (!is.null(line$tavotto_cap_spans)) return(line$tavotto_cap_spans[row])
  positions <- line[[caps$axis]]
  positions[caps$points[2L, row]] - positions[caps$points[1L, row]]
}

ggplot_edit_errorbar_caps <- function(line, caps, length_pt, width_pt) {
  points <- caps$points
  count <- ncol(points)
  authored <- line$tavotto_cap_style
  base <- if (is.null(line$tavotto_cap_base)) line else line$tavotto_cap_base
  line <- base
  length_pt <- rep_len(if (is.null(length_pt)) NA_real_ else length_pt, count)
  width_pt <- rep_len(if (is.null(width_pt)) NA_real_ else width_pt, count)
  if (!is.null(authored)) {
    absent <- is.na(length_pt)
    length_pt[absent] <- authored$length[absent]
    absent <- is.na(width_pt)
    width_pt[absent] <- authored$width[absent]
  }
  resized <- which(!is.na(length_pt))
  spans <- ggplot_cap_span(line, caps, seq_len(count))
  if (length(resized)) {
    spans[resized] <- grid::unit(length_pt[resized] / 72, "inches")
    positions <- line[[caps$axis]]
    centre <- positions[points[c(4L, 4L, 5L, 5L), resized, drop = FALSE]]
    offsets <- rep(c(-1, 1), 2L) * rep(length_pt[resized], each = 4L) / 144
    positions[points[c(1L, 2L, 7L, 8L), resized, drop = FALSE]] <- centre + grid::unit(offsets, "inches")
    line[[caps$axis]] <- positions
  }
  line$tavotto_cap_spans <- spans
  hidden <- !is.na(length_pt) & length_pt == 0
  if (any(hidden) || any(!is.na(width_pt))) {
    segments <- matrix(points[c(1L, 2L, 4L, 5L, 7L, 8L), , drop = FALSE], nrow = 2L)
    rows <- rep(seq_len(count), each = 3L)
    is_cap <- rep(c(TRUE, FALSE, TRUE), count)
    visible <- !hidden[rows] | !is_cap
    indices <- segments[, visible, drop = FALSE]
    line$x <- line$x[indices]
    line$y <- line$y[indices]
    line$id <- rep(seq_len(ncol(indices)), each = 2L)
    line$tavotto_cap_rows <- rep(rows[visible], each = 2L)
    for (key in names(line$gp)) {
      value <- line$gp[[key]]
      if (length(value) != 1L) {
        line$gp[[key]] <- rep_len(value, count)[rows[visible]]
      }
    }
    thickened <- is_cap[visible] & !is.na(width_pt[rows[visible]])
    if (any(thickened)) {
      stroke <- rep_len(line$gp$lwd, ncol(indices))
      stroke[thickened] <- width_pt[rows[visible][thickened]] / ggplot_lwd_pt
      line$gp$lwd <- stroke
    }
  }
  line$tavotto_cap_base <- base
  line$tavotto_cap_style <- list(length = length_pt, width = width_pt)
  line
}

ggplot_edit_errorbar_group_styles <- function(line, caps, values) {
  if (!is.null(values$linewidth) || !is.null(values$alpha) || !is.null(values$color)) {
    base <- if (is.null(line$tavotto_cap_base)) line else line$tavotto_cap_base
    count <- ncol(caps$points)
    if (!is.null(values$linewidth)) {
      width <- rep_len(base$gp$lwd, count)
      rows <- which(!is.na(values$linewidth))
      width[rows] <- values$linewidth[rows] / ggplot_lwd_pt
      base$gp$lwd <- width
    }
    if (!is.null(values$color) || !is.null(values$alpha)) {
      colours <- rep_len(base$gp$col, count)
      rows <- which(!is.na(values$color))
      if (length(rows)) {
        # Colour and opacity are separate edits, including when alpha is mapped per row.
        alpha <- grDevices::col2rgb(colours[rows], alpha = TRUE)[4L, ] / 255
        colours[rows] <- scales::alpha(values$color[rows], alpha)
      }
      rows <- which(!is.na(values$alpha))
      colours[rows] <- scales::alpha(colours[rows], values$alpha[rows])
      base$gp$col <- colours
    }
    line$tavotto_cap_base <- base
  }
  ggplot_edit_errorbar_caps(line, caps, values$capsize, values$cap_thickness)
}

ggplot_apply_errorbar_caps <- function(line, rows, layout, panel_id, values) {
  caps <- ggplot_errorbar_caps(line, rows$flipped_aes)
  if (is.null(caps)) stop("Tavotto could not match the original R error bar geometry.")
  ggplot_edit_errorbar_group_styles(line, caps, values)
}
