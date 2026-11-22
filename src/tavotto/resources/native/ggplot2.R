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

ggplot_text_slots <- c(
  "plot.title" = "title",
  "axis.x.bottom.title" = "xlab-b",
  "axis.x.top.title" = "xlab-t",
  "axis.y.left.title" = "ylab-l",
  "axis.y.right.title" = "ylab-r"
)

ggplot_text_target <- function(table, slot_name) {
  slot <- which(table$layout$name == slot_name)
  if (length(slot) != 1L) return(NULL)
  children <- table$grobs[[slot]]$children
  if (length(children) != 1L) return(NULL)
  text <- children[[1L]]
  if (!inherits(text, "text") || !is.character(text$label) ||
      length(text$label) != 1L || is.na(text$label) || !nzchar(text$label)) {
    return(NULL)
  }
  # Resolve R color names (such as grey42) for the canvas color picker.
  rgb <- tryCatch(grDevices::col2rgb(text$gp$col, alpha = TRUE),
                  error = function(error) NULL)
  if (is.null(rgb) || ncol(rgb) != 1L) return(NULL)
  color <- grDevices::rgb(rgb[1L], rgb[2L], rgb[3L],
                          alpha = if (rgb[4L] != 255L) rgb[4L] else NULL,
                          maxColorValue = 255)
  list(slot = slot, child = text$name, label = text$label, paint = text$gp, color = color)
}

measure_ggplot_text <- function(table, target, frame_pt) {
  box <- table$layout[target$slot, ]
  # gtable's makeContent names cell viewports in top-right-bottom-left order.
  cell <- paste0(box$name, ".", paste(c(box$t, box$r, box$b, box$l), collapse = "-"))
  wrapper <- table$grobs[[target$slot]]$name
  on.exit(grid::upViewport(0))
  grid::seekViewport(grid::vpPath(table$name, cell))
  text <- grid::grid.get(grid::gPath(table$name, cell, wrapper, target$child), strict = TRUE)
  lower <- grid::deviceLoc(grid::grobX(text, 180), grid::grobY(text, 270), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::grobX(text, 0), grid::grobY(text, 90), valueOnly = TRUE)
  frame_in <- frame_pt / 72
  c(lower$x / frame_in[[1L]], 1 - upper$y / frame_in[[2L]],
    (upper$x - lower$x) / frame_in[[1L]], (upper$y - lower$y) / frame_in[[2L]])
}

ggplot_text_targets <- function(table, frame_pt) {
  forced <- tryCatch({
    grid::grid.force()
    TRUE
  }, error = function(error) FALSE)
  if (!forced) return(list())
  targets <- list()
  for (gid in names(ggplot_text_slots)) {
    target <- ggplot_text_target(table, ggplot_text_slots[[gid]])
    if (is.null(target)) next
    bbox <- tryCatch(measure_ggplot_text(table, target, frame_pt),
                     error = function(error) NULL)
    if (length(bbox) == 4L && all(is.finite(bbox)) && all(bbox[3:4] > 0)) {
      target$bbox <- bbox
      targets[[gid]] <- target
    }
  }
  targets
}

capture_ggplot <- function(plot, frame_pt) {
  if (!inherits(plot, "ggplot")) stop("The selected object is not a ggplot.")
  measured <- with_svg_frame(frame_pt, function() {
    built <- ggplot2::ggplot_build(plot)
    table <- ggplot2::ggplot_gtable(built)
    grid::grid.newpage()
    grid::grid.draw(table)
    list(
      table = table,
      text_targets = ggplot_text_targets(table, frame_pt),
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

ggplot_table_for <- function(state, colors) {
  table <- state$table
  for (gid in names(colors)) {
    target <- state$text_targets[[gid]]
    if (is.null(target)) stop("The requested native R title is unavailable.")
    color <- colors[[gid]]
    if (!is.character(color) || length(color) != 1L ||
        is.na(color) || !grepl("^#[0-9A-Fa-f]{6}$", color)) {
      stop("The native R title color must be an RGB hex color.")
    }
    paint <- target$paint
    paint$col <- color
    table$grobs[[target$slot]] <- grid::editGrob(
      table$grobs[[target$slot]], target$child, gp = paint, strict = TRUE
    )
  }
  table
}

render_ggplot <- function(state, colors = list()) {
  table <- ggplot_table_for(state, colors)
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

export_ggplot_pdf <- function(state, colors, path) {
  table <- ggplot_table_for(state, colors)
  device <- open_ggplot_pdf(state$frame_pt, path)
  on.exit(grDevices::dev.off(which = device))
  grid::grid.newpage()
  grid::grid.draw(table)
  invisible(NULL)
}

ggplot_manifest <- function(state, stem, colors = list()) {
  elements <- lapply(names(state$text_targets), function(gid) {
    target <- state$text_targets[[gid]]
    role <- if (gid == "plot.title") "title" else "axis_label"
    color <- if (is.null(colors[[gid]])) target$color else colors[[gid]]
    list(
      gid = gid, role = role, label = target$label,
      bbox = as.list(target$bbox), draggable = FALSE,
      editable = list(list(prop = "color", type = "color", value = color))
    )
  })
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72), elements = elements)
}
