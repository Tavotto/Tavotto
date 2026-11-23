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
  "plot.subtitle" = "subtitle",
  "plot.caption" = "caption",
  "axis.x.bottom.title" = "xlab-b",
  "axis.x.top.title" = "xlab-t",
  "axis.y.left.title" = "ylab-l",
  "axis.y.right.title" = "ylab-r"
)

ggplot_text_target <- function(table, slots, role, title_child = NULL, group_label = NULL) {
  path <- character()
  for (slot in slots) {
    if (is.character(slot)) {
      path <- c(path, table$name)
      table <- table$children[[slot]]
      next
    }
    box <- table$layout[slot, ]
    # gtable names its drawn cells in top-right-bottom-left order.
    cell <- paste0(box$name, ".", paste(c(box$t, box$r, box$b, box$l), collapse = "-"))
    path <- c(path, table$name, cell)
    table <- table$grobs[[slot]]
  }
  if (!is.null(title_child)) {
    path <- c(path, table$name)
    table <- table$children[[title_child]]
  }
  children <- table$children
  if (length(children) != 1L) return(NULL)
  text <- children[[1L]]
  if (!inherits(text, "text")) return(NULL)
  label <- text$label
  if (is.null(group_label)) {
    if (!is.character(label) || length(label) != 1L || is.na(label) || !nzchar(label)) return(NULL)
  } else {
    if ((!is.character(label) && !is.numeric(label)) || !length(label) ||
        anyNA(label) || any(!nzchar(label)) || isTRUE(text$check.overlap)) return(NULL)
  }
  # Resolve R color names (such as grey42) for the canvas color picker.
  rgb <- tryCatch(grDevices::col2rgb(text$gp$col, alpha = TRUE),
                  error = function(error) NULL)
  if (is.null(rgb) || ncol(rgb) != 1L) return(NULL)
  color <- grDevices::rgb(rgb[1L], rgb[2L], rgb[3L],
                          alpha = if (rgb[4L] != 255L) rgb[4L] else NULL,
                          maxColorValue = 255)
  edit_path <- if (is.null(title_child)) text$name else grid::gPath(table$name, text$name)
  list(slots = slots, parent_path = paste(c(path, table$name), collapse = "::"),
       child = text$name, edit_path = edit_path,
       label = if (is.null(group_label)) label else group_label,
       tick_labels = if (!is.null(group_label)) as.list(as.character(label)) else NULL,
       paint = text$gp, color = color, role = role)
}

ggplot_axis_ticks <- function(table) {
  # A panel can repeat guides within a facet. Start with the four unique outer
  # cells of a single-panel plot, even when a cell contains a secondary axis.
  if (length(which(grepl("^panel(-[0-9]+-[0-9]+)?$", table$layout$name))) != 1L) return(list())
  # Keep generated Chinese labels portable when R sources this file in a non-UTF-8 locale.
  sides <- list(
    "axis.x.bottom.ticks" = c("axis-b", "\u4e0b"),
    "axis.x.top.ticks" = c("axis-t", "\u4e0a"),
    "axis.y.left.ticks" = c("axis-l", "\u5de6"),
    "axis.y.right.ticks" = c("axis-r", "\u53f3")
  )
  targets <- list()
  for (gid in names(sides)) {
    side <- sides[[gid]]
    slot <- which(table$layout$name == side[[1L]])
    if (length(slot) != 1L) next
    axis <- table$grobs[[slot]]$children[["axis"]]
    if (!inherits(axis, "gtable")) next
    labels <- which(vapply(axis$grobs, inherits, logical(1L), "titleGrob"))
    if (length(labels) != 1L) next
    label <- paste0(side[[2L]], "\u8f74\u523b\u5ea6\u6587\u5b57")
    targets[[gid]] <- ggplot_text_target(table, list(slot, "axis", labels), "axis_label",
                                         group_label = label)
  }
  targets
}

ggplot_legend_entries <- function(table, slots, guide, prefix, title) {
  cells <- guide$layout$name
  labels <- which(grepl("^label-[0-9]+-[0-9]+$", cells))
  keys <- cells[grepl("^key-[0-9]+-[0-9]+-bg$", cells)]
  # A standard keyed legend has one cell per key and one per label.
  if (!length(labels) || length(labels) != length(keys) ||
      anyDuplicated(keys) || anyDuplicated(cells[labels])) return(list())
  targets <- list()
  for (slot in labels) {
    target <- ggplot_text_target(table, c(slots, slot), "legend_text")
    if (is.null(target)) next
    cell <- sub("^label-", "", cells[[slot]])
    gid <- paste0(prefix, ".entry.", gsub("-", ".", cell, fixed = TRUE))
    target$legend_title <- title
    targets[[gid]] <- target
  }
  text <- vapply(targets, function(target) target$label, character(1L))
  targets[!(duplicated(text) | duplicated(text, fromLast = TRUE))]
}

ggplot_legend_targets <- function(table) {
  titles <- list()
  entries <- list()
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
      slots <- c(box_slot, guide_slot)
      target <- ggplot_text_target(table, c(slots, title_slot), "legend_text")
      if (is.null(target)) next
      prefix <- paste("legend", position, index, sep = ".")
      gid <- paste0(prefix, ".title")
      titles[[gid]] <- target
      entries[[gid]] <- ggplot_legend_entries(table, slots, guide, prefix, target$label)
    }
  }
  # An index and a label cannot distinguish same-named guides after reordering.
  labels <- vapply(titles, function(target) target$label, character(1L))
  titles <- titles[!(duplicated(labels) | duplicated(labels, fromLast = TRUE))]
  c(titles, unlist(unname(entries[names(titles)]), recursive = FALSE))
}

ggplot_facet_labels <- function(table) {
  targets <- list()
  keys <- character()
  cells <- table$layout$name
  slots <- which(grepl("^strip-[tblr]-[0-9]+(-[0-9]+)?$", cells) &
                   !duplicated(cells) & !duplicated(cells, fromLast = TRUE))
  for (slot in slots) {
    strip <- table$grobs[[slot]]
    if (!inherits(strip, "gtable") || length(strip$grobs) != 1L ||
        !identical(strip$layout$name, "strip")) next
    titles <- which(vapply(strip$grobs[[1L]]$children, inherits, logical(1L), "titleGrob"))
    if (length(titles) != 1L) next
    target <- ggplot_text_target(table, c(slot, 1L), "text", title_child = titles[[1L]])
    if (is.null(target)) next
    position <- sub("^strip-", "", cells[[slot]])
    gid <- paste0("facet.", gsub("-", ".", position, fixed = TRUE), ".label")
    targets[[gid]] <- target
    keys <- c(keys, paste(substr(position, 1L, 1L), target$label, sep = ":"))
  }
  # Reordered strips with duplicate labels on the same side have no stable target.
  targets[!(duplicated(keys) | duplicated(keys, fromLast = TRUE))]
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
    role <- if (gid == "plot.title") "title" else if (startsWith(gid, "axis.")) "axis_label" else "text"
    targets[[gid]] <- ggplot_text_target(table, slot, role)
  }
  targets <- c(targets, ggplot_legend_targets(table), ggplot_facet_labels(table),
               ggplot_axis_ticks(table))
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

edit_ggplot_text <- function(table, slots, path, paint) {
  if (!length(slots)) return(grid::editGrob(table, path, gp = paint, strict = TRUE))
  slot <- slots[[1L]]
  if (is.character(slot)) {
    table$children[[slot]] <- edit_ggplot_text(table$children[[slot]], slots[-1L], path, paint)
  } else {
    table$grobs[[slot]] <- edit_ggplot_text(table$grobs[[slot]], slots[-1L], path, paint)
  }
  table
}

ggplot_table_for <- function(state, colors) {
  table <- state$table
  for (gid in names(colors)) {
    target <- state$text_targets[[gid]]
    if (is.null(target)) stop("The requested native R text is unavailable.")
    color <- colors[[gid]]
    if (!is.character(color) || length(color) != 1L ||
        is.na(color) || !grepl("^#[0-9A-Fa-f]{6}$", color)) {
      stop("The native R text color must be an RGB hex color.")
    }
    paint <- target$paint
    paint$col <- color
    table <- edit_ggplot_text(table, target$slots, target$edit_path, paint)
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
    element <- list(
      gid = gid, role = target$role, label = target$label,
      bbox = as.list(target$bbox), draggable = FALSE,
      editable = list(list(prop = "color", type = "color", value = color))
    )
    if (!is.null(target$legend_title)) element$legend_title <- target$legend_title
    if (!is.null(target$tick_labels)) element$tick_labels <- target$tick_labels
    element
  })
  list(stem = stem, size_mm = as.list(state$frame_pt * 25.4 / 72), elements = elements)
}
