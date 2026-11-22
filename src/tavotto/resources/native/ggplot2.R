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

ggplot_text_target <- function(table, slots, role) {
  path <- character()
  for (slot in slots) {
    box <- table$layout[slot, ]
    # gtable names its drawn cells in top-right-bottom-left order.
    cell <- paste0(box$name, ".", paste(c(box$t, box$r, box$b, box$l), collapse = "-"))
    path <- c(path, table$name, cell)
    table <- table$grobs[[slot]]
  }
  children <- table$children
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
  list(slots = slots, parent_path = paste(c(path, table$name), collapse = "::"),
       child = text$name, label = text$label, paint = text$gp, color = color, role = role)
}

ggplot_legend_titles <- function(table) {
  targets <- list()
  for (box_slot in which(startsWith(table$layout$name, "guide-box-"))) {
    box <- table$grobs[[box_slot]]
    if (!inherits(box, "gtable")) next
    position <- sub("^guide-box-", "", table$layout$name[[box_slot]])
    guides <- which(box$layout$name == "guides")
    for (index in seq_along(guides)) {
      guide_slot <- guides[[index]]
      guide <- box$grobs[[guide_slot]]
      if (!inherits(guide, "gtable")) next
      title_slot <- which(guide$layout$name == "title")
      if (length(title_slot) != 1L) next
      target <- ggplot_text_target(table, c(box_slot, guide_slot, title_slot), "legend_text")
      if (is.null(target)) next
      gid <- paste("legend", position, index, "title", sep = ".")
      targets[[gid]] <- target
    }
  }
  # An index and a label cannot distinguish same-named guides after reordering.
  labels <- vapply(targets, function(target) target$label, character(1L))
  targets[!(duplicated(labels) | duplicated(labels, fromLast = TRUE))]
}

measure_ggplot_text <- function(drawn, target, frame_pt) {
  row <- which(drawn$name == target$child & drawn$gPath == target$parent_path)
  if (length(row) != 1L) return(NULL)
  # Nested guides can insert viewports that are not part of their grob paths.
  viewport <- strsplit(drawn$vpPath[[row]], "::", fixed = TRUE)[[1L]]
  if (length(viewport) < 2L || viewport[[1L]] != "ROOT") return(NULL)
  on.exit(grid::upViewport(0))
  grid::downViewport(do.call(grid::vpPath, as.list(viewport[-1L])), strict = TRUE)
  text <- grid::grid.get(grid::gPath(target$parent_path, target$child), strict = TRUE)
  lower <- grid::deviceLoc(grid::grobX(text, 180), grid::grobY(text, 270), valueOnly = TRUE)
  upper <- grid::deviceLoc(grid::grobX(text, 0), grid::grobY(text, 90), valueOnly = TRUE)
  frame_in <- frame_pt / 72
  c(lower$x / frame_in[[1L]], 1 - upper$y / frame_in[[2L]],
    (upper$x - lower$x) / frame_in[[1L]], (upper$y - lower$y) / frame_in[[2L]])
}

ggplot_text_targets <- function(table, frame_pt) {
  targets <- list()
  for (gid in names(ggplot_text_slots)) {
    slot <- which(table$layout$name == ggplot_text_slots[[gid]])
    if (length(slot) != 1L) next
    role <- if (gid == "plot.title") "title" else "axis_label"
    targets[[gid]] <- ggplot_text_target(table, slot, role)
  }
  targets <- c(targets, ggplot_legend_titles(table))
  if (!length(targets)) return(targets)
  drawn <- tryCatch({
    grid::grid.force()
    grid::grid.ls(viewports = TRUE, print = FALSE)
  }, error = function(error) NULL)
  if (is.null(drawn)) return(list())
  for (gid in names(targets)) {
    bbox <- tryCatch(measure_ggplot_text(drawn, targets[[gid]], frame_pt),
                     error = function(error) NULL)
    if (length(bbox) == 4L && all(is.finite(bbox)) && all(bbox[3:4] > 0)) {
      targets[[gid]]$bbox <- bbox
    } else {
      targets[[gid]] <- NULL
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

edit_ggplot_title <- function(table, slots, child, paint) {
  if (!length(slots)) return(grid::editGrob(table, child, gp = paint, strict = TRUE))
  slot <- slots[[1L]]
  table$grobs[[slot]] <- edit_ggplot_title(table$grobs[[slot]], slots[-1L], child, paint)
  table
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
    table <- edit_ggplot_title(table, target$slots, target$child, paint)
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
    color <- if (is.null(colors[[gid]])) target$color else colors[[gid]]
    list(
      gid = gid, role = target$role, label = target$label,
      bbox = as.list(target$bbox), draggable = FALSE,
      editable = list(list(prop = "color", type = "color", value = color))
    )
  })
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72), elements = elements)
}
