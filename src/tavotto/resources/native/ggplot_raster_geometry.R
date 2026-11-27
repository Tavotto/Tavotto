# Preserve native Geom methods so the editor and exported R script share
# the same tested coordinate and tick behavior after reopening.
ggplot_stock_geom <- function(geom, kind) {
  if (!identical(class(geom)[[1L]], kind)) return(FALSE)
  base <- getExportedValue("ggplot2", kind)
  methods <- c("draw_layer", "draw_panel", "draw_group", "handle_na")
  identical(as.list(geom)[methods], as.list(base)[methods])
}

# Raster columns and rows are indexed before the coordinate transform.
# Draw with forward views, then reflect the raster and its placement.
ggplot_mirror_raster_layer <- function(layer, coord) {
  if (inherits(layer, "TavottoRasterReflection") ||
      !identical(class(coord)[[1L]], "CoordCartesian") ||
      !ggplot_stock_geom(layer$geom, "GeomRaster")) return(layer)
  force(layer)
  ggplot2::ggproto("TavottoRasterReflection", layer, draw_geom = function(self, data, layout) {
    parent <- ggplot2::ggproto_parent(layer, self)
    panels <- layout$panel_params
    directions <- vapply(panels, function(panel) panel$reverse, "")
    if (all(directions == "none")) return(parent$draw_geom(data, layout))
    forward <- lapply(panels, function(panel) {
      panel$reverse <- "none"
      panel
    })
    views <- ggplot2::ggproto(NULL, layout, panel_params = forward)
    grobs <- parent$draw_geom(data, views)
    if (all(vapply(grobs, inherits, logical(1L), "zeroGrob"))) return(grobs)
    if (is.null(names(grobs))) {
      ids <- unique(as.integer(as.character(data$PANEL)))
    } else {
      ids <- as.integer(names(grobs))
    }
    if (length(ids) != length(grobs) || anyNA(ids) || any(!ids %in% seq_along(panels))) {
      stop("The native R raster no longer matches its captured panels.")
    }
    Map(function(grob, direction) {
      if (direction == "none" || inherits(grob, "zeroGrob")) return(grob)
      if (!inherits(grob, "rastergrob") || !identical(grob$just, "centre") ||
          !is.null(grob$vp) || !is.null(grob$hjust) || !is.null(grob$vjust) ||
          !identical(grid::unitType(grob$x), "native") ||
          !identical(grid::unitType(grob$y), "native")) {
        stop("The native R raster no longer has a reversible placement.")
      }
      rows <- seq_len(nrow(grob$raster))
      cols <- seq_len(ncol(grob$raster))
      if (grepl("x", direction, fixed = TRUE)) {
        cols <- rev(cols)
        grob$x <- grid::unit(1 - as.numeric(grob$x), "native")
      }
      if (grepl("y", direction, fixed = TRUE)) {
        rows <- rev(rows)
        grob$y <- grid::unit(1 - as.numeric(grob$y), "native")
      }
      grob$raster <- grDevices::as.raster(grob$raster[rows, cols, drop = FALSE])
      grob
    }, grobs, directions[ids])
  })
}
