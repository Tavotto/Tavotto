ggplot_uniform_pigment <- function(colour) {
  if (is.logical(colour) && length(colour) && all(is.na(colour))) {
    return(list(rgb = "none", alpha = 0))
  }
  if (!is.character(colour) || !length(colour)) return(NULL) # grid pattern or gradient
  colours <- unique(colour)
  rgba <- grDevices::col2rgb(colours, alpha = TRUE)
  if (!all(rgba == rgba[, 1L])) return(NULL)
  alpha <- rgba[4L, 1L] / 255
  list(rgb = if (alpha == 0) "none" else grDevices::rgb(rgba[1L, 1L], rgba[2L, 1L],
                                                       rgba[3L, 1L], maxColorValue = 255),
       alpha = alpha)
}

ggplot_edit_pigment <- function(value, original, alpha, layer_alpha) {
  if (value == "none") return(NA_character_)
  if (is.null(alpha)) alpha <- if (original$alpha > 0) original$alpha else layer_alpha
  if (is.null(alpha)) alpha <- 1
  scales::alpha(value, alpha)
}
