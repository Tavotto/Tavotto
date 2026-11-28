ggplot_point_markers <- c("o" = 19L, "s" = 15L, "^" = 17L, "D" = 18L)

ggplot_point_aesthetic_size <- function(area) sqrt(area) / ggplot2::.pt

ggplot_point_aesthetic_stroke <- function(width) width / ggplot_lwd_pt * 2 / ggplot2::.stroke

ggplot_point_marker <- function(pch) {
  if (length(pch) != 1L || !is.numeric(pch) || !is.finite(pch)) return(NULL)
  if (pch == 16L) pch <- 19L
  at <- match(pch, ggplot_point_markers)
  if (!is.na(at)) names(ggplot_point_markers)[[at]]
}

ggplot_point_paint_channels <- function(pch) {
  if (length(pch) && all(pch %in% 21:25)) return(c(facecolor = "fill", edgecolor = "col"))
  if (length(pch) && all(pch %in% 15:20)) return(c(facecolor = "col"))
  character()
}

ggplot_style_point_key <- function(params, key, patch) {
  data <- params$decor$geom_point$data
  if (!is.null(patch$marker) && patch$marker != "original") {
    shape <- ggplot_point_markers[[patch$marker]]
    if ("shape" %in% names(params$key)) params$key$shape[[key]] <- shape
    data$shape[[key]] <- shape
  }
  for (prop in intersect(names(patch), c("facecolor", "edgecolor"))) {
    channel <- ggplot_point_paint_channels(data$shape[[key]])[[prop]]
    field <- if (channel == "fill") "fill" else "colour"
    original <- ggplot_uniform_pigment(data[[field]][[key]])
    data[[field]][[key]] <- ggplot_edit_pigment(patch[[prop]], original, patch$alpha, data$alpha[[key]])
  }
  if (!is.null(patch$size)) data$size[[key]] <- ggplot_point_aesthetic_size(patch$size)
  if (!is.null(patch$linewidth)) {
    data$stroke[[key]] <- ggplot_point_aesthetic_stroke(patch$linewidth)
  }
  if (!is.null(patch$alpha)) data$alpha[[key]] <- patch$alpha
  params$decor$geom_point$data <- data
  params
}

ggplot_style_point_grob <- function(points, count, rows, values, edit, original_alpha) {
  patch <- function(current, value) {
    if (is.null(rows)) return(value)
    current <- rep_len(current, count)
    current[rows] <- value
    current
  }
  select <- function(value) if (is.null(rows)) value else rep_len(value, count)[rows]
  if (!is.null(edit$size) || !is.null(edit$linewidth)) {
    stroke <- select(points$gp$lwd)
    size <- select(points$gp$fontsize) - stroke
    if (!is.null(edit$size)) size <- sqrt(values$size)
    if (!is.null(edit$linewidth)) {
      stroke <- values$linewidth / ggplot_lwd_pt
      points$gp$lwd <- patch(points$gp$lwd, stroke)
    }
    points$gp$fontsize <- patch(points$gp$fontsize, size + stroke)
  }
  channels <- ggplot_point_paint_channels(unique(select(points$pch)))
  for (prop in intersect(names(edit), names(channels))) {
    channel <- channels[[prop]]
    value <- points$gp[[channel]]
    original <- ggplot_uniform_pigment(select(value))
    pigment <- ggplot_edit_pigment(values[[prop]], original, edit$alpha, original_alpha)
    points$gp[[channel]] <- patch(value, pigment)
  }
  if (!is.null(edit$marker) && values$marker != "original") {
    points$pch <- patch(points$pch, ggplot_point_markers[[values$marker]])
  }
  if (!is.null(edit$alpha)) {
    fields <- if (length(channels)) unname(channels) else c("col", "fill")
    for (field in fields) {
      pigment <- points$gp[[field]]
      if (!length(pigment)) next
      points$gp[[field]] <- patch(pigment, scales::alpha(select(pigment), values$alpha))
    }
  }
  points
}

ggplot_apply_point_styles <- function(points, rows, layout, panel, values, local = FALSE) {
  projected <- layout$coord$transform(rows[c("x", "y")], layout$panel_params[[panel]])
  count <- nrow(rows)
  if (!inherits(points, "points") || length(points$x) != count || length(points$y) != count ||
      !isTRUE(all.equal(as.numeric(points$x), as.numeric(projected$x), tolerance = 1e-12)) ||
      !isTRUE(all.equal(as.numeric(points$y), as.numeric(projected$y), tolerance = 1e-12))) {
    stop("Tavotto could not match the R point geometry.")
  }
  shared <- points$tavotto_point_shared
  if (local) {
    shared <- shared %||% points
  } else if (!is.null(shared)) {
    shared <- ggplot_record_point_styles(shared, rows, values)
  }
  result <- ggplot_record_point_styles(points, rows, values)
  result$tavotto_point_shared <- shared
  result
}

ggplot_record_point_styles <- function(points, rows, values) {
  count <- nrow(rows)
  base <- points$tavotto_point_base %||% points
  alpha <- points$tavotto_point_alpha %||% rep_len(rows$alpha, count)
  edits <- as.data.frame(values)
  combinations <- unique(edits)
  for (index in seq_len(nrow(combinations))) {
    combination <- combinations[index, , drop = FALSE]
    patch <- Filter(function(value) !is.na(value), as.list(combination))
    if (!length(patch)) next
    same <- Map(function(column, value) {
      if (is.na(value)) is.na(column) else !is.na(column) & column == value
    }, edits, combination)
    selected <- which(Reduce(`&`, same))
    original <- unique(alpha[selected])
    original <- if (length(original) == 1L && is.finite(original)) original
    points <- ggplot_style_point_grob(points, count, selected, patch, patch, original)
    if (!is.null(patch$alpha)) alpha[selected] <- patch$alpha
  }
  points$tavotto_point_base <- base
  points$tavotto_point_alpha <- alpha
  points
}

ggplot_point_guide_recipe <- function(aesthetics, recipes) {
  list(key = "geom_point", aesthetics = aesthetics, recipes = recipes, apply = ggplot_style_point_key)
}
