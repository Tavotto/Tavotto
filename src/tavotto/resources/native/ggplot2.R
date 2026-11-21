# The adapter receives a plot from an already authorized R invocation. Building
# the layout and applying edits are separate so a paint change never reruns a stat.

with_svg_frame <- function(frame_pt, draw) {
  if (!is.numeric(frame_pt) || length(frame_pt) != 2L ||
      any(!is.finite(frame_pt)) || any(frame_pt <= 0)) {
    stop("The SVG frame needs two positive dimensions in points.")
  }
  capture <- svglite::svgstring(width = frame_pt[[1L]] / 72,
                               height = frame_pt[[2L]] / 72)
  device <- grDevices::dev.cur()
  on.exit({
    if (device %in% grDevices::dev.list()) grDevices::dev.off(which = device)
  })
  value <- draw()
  grDevices::dev.off(which = device)
  list(value = value, svg = as.character(capture()))
}

ggplot_title <- function(table) {
  slot <- which(table$layout$name == "title")
  if (length(slot) != 1L) return(NULL)
  children <- table$grobs[[slot]]$children
  if (length(children) != 1L) return(NULL)
  text <- children[[1L]]
  if (!inherits(text, "text") || !is.character(text$label) ||
      length(text$label) != 1L || is.na(text$label)) {
    return(NULL)
  }
  list(slot = slot, child = text$name, label = text$label, paint = text$gp)
}

measure_ggplot_title <- function(table, title, frame_pt) {
  if (is.null(title)) return(NULL)
  box <- table$layout[title$slot, ]
  # gtable's makeContent names cell viewports in top-right-bottom-left order.
  cell <- paste0("title.", paste(c(box$t, box$r, box$b, box$l), collapse = "-"))
  caption <- table$grobs[[title$slot]]$name
  grid::grid.force()
  grid::seekViewport(grid::vpPath(table$name, cell))
  on.exit(grid::upViewport(0))
  text <- grid::grid.get(grid::gPath(table$name, cell, caption, title$child), strict = TRUE)
  lower <- grid::deviceLoc(grid::grobX(text, 180), grid::grobY(text, 270), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::grobX(text, 0), grid::grobY(text, 90), valueOnly = TRUE)
  frame_in <- frame_pt / 72
  c(lower$x / frame_in[[1L]], 1 - upper$y / frame_in[[2L]],
    (upper$x - lower$x) / frame_in[[1L]], (upper$y - lower$y) / frame_in[[2L]])
}

capture_ggplot <- function(plot, frame_pt) {
  if (!inherits(plot, "ggplot")) stop("The selected object is not a ggplot.")
  measured <- with_svg_frame(frame_pt, function() {
    built <- ggplot2::ggplot_build(plot)
    table <- ggplot2::ggplot_gtable(built)
    title <- ggplot_title(table)
    grid::grid.newpage()
    grid::grid.draw(table)
    title_bbox <- tryCatch(measure_ggplot_title(table, title, frame_pt),
                           error = function(error) NULL)
    list(
      table = table,
      title = title,
      title_bbox = title_bbox,
      layers = built$data,
      panel_ranges = lapply(built$layout$panel_params, function(panel) {
        list(x = panel$x.range, y = panel$y.range)
      })
    )
  })
  # Measure on the same device type and frame as the final draw. Use a fresh
  # device for the artifact so viewport inspection does not add SVG nodes.
  state <- c(list(frame_pt = frame_pt), measured$value)
  list(state = state, svg = render_ggplot(state))
}

ggplot_table_for <- function(state, title_color) {
  table <- state$table
  if (!is.null(title_color)) {
    target <- state$title
    if (is.null(target)) stop("A single plain-text plot title is required.")
    if (!is.character(title_color) || length(title_color) != 1L ||
        is.na(title_color) || !grepl("^#[0-9A-Fa-f]{6}$", title_color)) {
      stop("The plot title color must be an RGB hex color.")
    }
    paint <- target$paint
    paint$col <- title_color
    table$grobs[[target$slot]] <- grid::editGrob(
      table$grobs[[target$slot]], target$child, gp = paint, strict = TRUE
    )
  }
  table
}

render_ggplot <- function(state, title_color = NULL) {
  table <- ggplot_table_for(state, title_color)
  with_svg_frame(state$frame_pt, function() {
    grid::grid.newpage()
    grid::grid.draw(table)
  })$svg
}

open_ggplot_pdf <- function(frame_pt, path) {
  if (!capabilities("cairo")) stop("The selected R cannot produce Cairo PDF.")
  before <- grDevices::dev.cur()
  diagnostics <- character()
  withCallingHandlers(
    grDevices::cairo_pdf(filename = path, width = frame_pt[[1L]] / 72,
                         height = frame_pt[[2L]] / 72, onefile = TRUE),
    warning = function(warning) diagnostics <<- c(diagnostics, conditionMessage(warning))
  )
  after <- grDevices::dev.cur()
  if (identical(before, after)) {
    stop(paste(c("The selected R could not open its Cairo PDF device.", diagnostics),
               collapse = " "))
  }
  after
}

export_ggplot_pdf <- function(state, title_color, path) {
  table <- ggplot_table_for(state, title_color)
  device <- open_ggplot_pdf(state$frame_pt, path)
  on.exit(grDevices::dev.off(which = device))
  grid::grid.newpage()
  grid::grid.draw(table)
  invisible(NULL)
}

ggplot_manifest <- function(state, stem, title_color = NULL) {
  elements <- list()
  if (!is.null(state$title_bbox)) {
    color <- if (is.null(title_color)) state$title$paint$col else title_color
    elements <- list(list(
      gid = "plot.title", role = "title", label = state$title$label,
      bbox = as.list(state$title_bbox), draggable = FALSE,
      editable = list(list(prop = "color", type = "color", value = color))
    ))
  }
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72), elements = elements)
}
