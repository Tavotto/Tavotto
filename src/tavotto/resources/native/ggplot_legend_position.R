makeContext.tavottoLegendPosition <- function(x) {
  # Grid calls makeContext while still in the original gtable cell. Resolve
  # the absolute figure anchor against that cell on the actual output device.
  p <- x$tavotto_position
  frame <- grDevices::dev.size("in")
  origin <- grid::deviceLoc(grid::unit(0, "npc"), grid::unit(0, "npc"), valueOnly = TRUE)
  xy <- c(p[[1L]], 1 - p[[2L]]) * frame - c(origin$x, origin$y)
  if (any(!is.finite(xy))) stop("The native R legend position is outside the device range.")
  just <- x$vp$valid.just
  x$vp$x <- grid::unit(xy[[1L]], "in") + just[[1L]] * x$vp$width
  x$vp$y <- grid::unit(xy[[2L]], "in") + just[[2L]] * x$vp$height
  NextMethod()
}

registerS3method("makeContext", "tavottoLegendPosition", makeContext.tavottoLegendPosition,
                 envir = asNamespace("grid"))

ggplot_move_legend_box <- function(box, anchor) {
  box$tavotto_position <- anchor
  if (!inherits(box, "tavottoLegendPosition")) class(box) <- c("tavottoLegendPosition", class(box))
  box
}

ggplot_legend_box_titles <- function(box) {
  if (!inherits(box, "gtable") || !inherits(box$vp, "viewport") || box$vp$angle != 0) return(NULL)
  guides <- box$grobs[box$layout$name == "guides"]
  if (!length(guides) || !all(vapply(guides, inherits, logical(1L), "gtable"))) return(NULL)
  titles <- vapply(guides, function(guide) {
    slot <- which(guide$layout$name == "title")
    if (length(slot) != 1L) return(NA_character_)
    children <- guide$grobs[[slot]]$children
    if (length(children) != 1L || !inherits(children[[1L]], "text")) return(NA_character_)
    label <- children[[1L]]$label
    if (!is.character(label) || length(label) != 1L || is.na(label) || !nzchar(label)) {
      return(NA_character_)
    }
    label
  }, character(1L), USE.NAMES = FALSE)
  if (!anyNA(titles) && !anyDuplicated(titles)) titles
}

# Keep the original layout cell and move the box on the output device.
ggplot_with_legend_positions <- function(plot, recipes) {
  original <- plot$guides
  force(recipes)
  plot$guides <- ggplot_clone(original, build = function(self, ...) {
    trained <- ggplot2::ggproto_parent(original, self)$build(...)
    ggplot_clone(trained, assemble = function(self, ...) {
      boxes <- ggplot2::ggproto_parent(trained, self)$assemble(...)
      for (recipe in recipes) {
        box <- boxes[[recipe$side]]
        if (!identical(ggplot_legend_box_titles(box), recipe$titles)) {
          stop("Tavotto could not match the R legend box.")
        }
        boxes[[recipe$side]] <- ggplot_move_legend_box(box, recipe$anchor)
      }
      boxes
    })
  })
  plot
}
