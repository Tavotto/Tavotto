# One grid stroke conversion for native edits and generated R code.
ggplot_lwd_pt <- 72 / 96

ggplot_clone <- function(parent, ...) {
  force(parent)
  ggplot2::ggproto(NULL, parent, ...)
}
