# Guide hashes include trained breaks. Save the actual relative order so
# writing a scale adjustment cannot move it past another guide.
ggplot_native_guide_order <- function(guides) {
  if (!length(guides$guides) || length(guides$guides) != length(guides$params)) return(NULL)
  keys <- mapply(function(guide, params) {
    aesthetics <- params$aesthetic
    if (!is.character(aesthetics) || !length(aesthetics) || anyNA(aesthetics)) return("")
    paste(class(guide)[[1L]], paste(sort(aesthetics), collapse = ","), sep = ":")
  }, unname(guides$guides), unname(guides$params), USE.NAMES = FALSE)
  if (all(nzchar(keys)) && !anyDuplicated(keys)) keys
}

ggplot_preserve_guide_order <- function(plot, keys) {
  original <- plot$guides
  plot$guides <- ggplot2::ggproto(NULL, original, build = function(self, ...) {
    built <- ggplot2::ggproto_parent(original, self)$build(...)
    current <- ggplot_native_guide_order(built)
    positions <- match(keys, current)
    if (length(current) != length(keys) || anyNA(positions)) return(built)
    built$guides <- built$guides[positions]
    built$params <- built$params[positions]
    if (length(built$aesthetics) == length(positions)) built$aesthetics <- built$aesthetics[positions]
    built
  })
  plot
}
