"""Real R acceptance for an edit of an already built ggplot2 scene."""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
import pytest
from PIL import Image

from tavotto.engine import (
    desktopproc,
    envlease,
    execspec,
    figcapture,
    nativeggplot,
    nativesession,
    pool,
)

# Cold graphics-device and font discovery can exceed 40 seconds on hosted Intel Macs.
_COLD_RENDER_TIMEOUT_SECONDS = 120


def _rscript():
    explicit = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    executable = explicit or shutil.which("Rscript")
    if executable is None:
        pytest.skip("real Rscript runtime is unavailable")
    return executable, explicit


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "src" / "tavotto" / "resources" / "native" / "ggplot2.R"
SUBTITLE = "plot.subtitle"
CAPTION = "plot.caption"
TICKS_BOTTOM = "axis.x.bottom.ticks"
TICKS_TOP = "axis.x.top.ticks"
COLOURBAR_TICKS = "legend.bottom.1.colourbar.ticks"
X_BOTTOM = "axis.x.bottom.title"
Y_LEFT = "axis.y.left.title"
LEGEND = "legend.right.1.title"
LEGEND_ENTRY = "legend.right.1.entry.3.2"
LEGEND_BOX = "legend.right"
FACET = "facet.t.2.2.label"


def _pdf_text_colors(path):
    with pymupdf.open(path) as doc:
        return {
            "".join(span["text"] for span in line["spans"]): {
                span["color"] for span in line["spans"]
            }
            for page in doc
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
        }


PROBE = r"""
adapter <- new.env(parent = baseenv())
sys.source(commandArgs(trailingOnly = TRUE)[[1L]], envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_points.R"), envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_bars.R"), envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_ribbons.R"), envir = adapter)
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) {
  quit(status = 77L)
}

color_edits <- function(colors) lapply(colors, function(value) list(color = value))
render_colors <- function(state, colors = list()) adapter$render_ggplot(state, color_edits(colors))

summary_calls <- 0L
tracked_mean <- function(values) {
  summary_calls <<- summary_calls + 1L
  mean(values)
}
set.seed(32026)
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point(ggplot2::aes(shape = factor(cyl)),
                      position = ggplot2::position_jitter(width = .12, height = 0)) +
  ggplot2::stat_summary(fun = tracked_mean, geom = "point", shape = 4) +
  ggplot2::scale_colour_discrete(labels = c("Four wheel drive", "Front wheel drive",
                                           "Rear wheel drive")) +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type", shape = "Cylinders") +
  ggplot2::guides(
    colour = ggplot2::guide_legend(order = 1, theme = ggplot2::theme(
      legend.title = ggplot2::element_text(colour = "#667788"),
      legend.text = ggplot2::element_text(colour = "#7D6439"))),
    shape = ggplot2::guide_legend(order = 2, theme = ggplot2::theme(
      legend.title = ggplot2::element_text(colour = "#778899")))
  ) +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis(name = "Secondary horizontal")) +
  ggplot2::scale_y_continuous(sec.axis = ggplot2::dup_axis(name = "Secondary vertical")) +
  ggplot2::theme_bw() +
  ggplot2::theme(
    plot.title = ggplot2::element_text(colour = "#112233"),
    axis.title.x.bottom = ggplot2::element_text(colour = "#223344"),
    axis.title.x.top = ggplot2::element_text(colour = "#334455"),
    axis.title.y.left = ggplot2::element_text(colour = "#445566"),
    axis.title.y.right = ggplot2::element_text(colour = "#556677"),
    strip.text = ggplot2::element_text(colour = "#202040")
  )

baseline <- adapter$capture_ggplot(plot, c(432, 288))
labels <- c("plot.title" = "Fuel economy",
            "axis.x.bottom.title" = "Engine displacement",
            "axis.x.top.title" = "Secondary horizontal",
            "axis.y.left.title" = "Highway mileage",
            "axis.y.right.title" = "Secondary vertical",
            "legend.right.1.title" = "Drive type",
            "legend.right.2.title" = "Cylinders",
            "legend.right.1.entry.3.2" = "Front wheel drive",
            "facet.t.2.2.label" = "Cylinder 8")
facets <- c("facet.t.1.2.label" = "Cylinder 6", "facet.t.2.2.label" = "Cylinder 8",
            "facet.t.1.1.label" = "Cylinder 4", "facet.t.2.1.label" = "Cylinder 5")
original <- c("#112233", "#223344", "#334455", "#445566", "#556677",
              "#667788", "#778899", "#7D6439", "#202040")
colors <- setNames(as.list(c("#6C3799", "#8C3011", "#2370B1", "#2A7654",
                            "#923190", "#3373A7", "#C75052", "#E058A9", "#BE3D47")),
                   names(labels))
entries <- function(state) {
  state$text_targets[grepl("\\.entry\\.", names(state$text_targets))]
}
stopifnot(summary_calls > 0L, length(baseline$state$layers) == 2L,
          length(baseline$state$panel_ranges) == 4L,
          identical(setdiff(names(baseline$state$text_targets), names(entries(baseline$state))),
                    c(head(names(labels), -2L), names(facets))),
          length(entries(baseline$state)) == 7L,
          identical(baseline$state$text_targets[["legend.right.1.entry.3.2"]]$legend_title,
                    "Drive type"),
          identical(vapply(baseline$state$text_targets[names(facets)], function(t) t$label,
                           character(1L)), facets))
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
edited <- render_colors(baseline$state, colors)
undone <- render_colors(baseline$state)
restored <- edited
for (i in seq_along(labels)) {
  gid <- names(labels)[[i]]
  label <- paste0(">", labels[[i]], "</text>")
  baseline_line <- grep(label, strsplit(baseline$svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
  edited_line <- grep(label, strsplit(edited, "\n")[[1L]], value = TRUE, fixed = TRUE)
  stopifnot(length(baseline_line) == 1L, grepl(original[[i]], baseline_line, fixed = TRUE),
            identical(edited_line, sub(original[[i]], colors[[gid]], baseline_line, fixed = TRUE)))
  # Use the SVG's text anchor and the measured extents to check each hit region.
  bbox <- baseline$state$text_targets[[gid]]$bbox * c(432, 288, 432, 288)
  if (startsWith(gid, "axis.y.")) {
    anchor <- as.numeric(strsplit(sub(".*translate\\(([^)]+)\\).*", "\\1", edited_line), ",")[[1L]])
    stopifnot(bbox[[4L]] > bbox[[3L]], abs(anchor[[2L]] - bbox[[2L]] - bbox[[4L]] / 2) < 1)
  } else {
    anchor <- as.numeric(sub(".* x='([^']+)'.*", "\\1", edited_line))
    centered <- startsWith(gid, "axis.x.") || startsWith(gid, "facet.")
    expected_x <- bbox[[1L]] + if (centered) bbox[[3L]] / 2 else 0
    stopifnot(bbox[[3L]] > bbox[[4L]], abs(anchor - expected_x) < 1)
    if (startsWith(gid, "legend.")) {
      y <- as.numeric(sub(".* y='([^']+)'.*", "\\1", edited_line))
      stopifnot(y >= bbox[[2L]] - 1, y <= bbox[[2L]] + bbox[[4L]] + 1)
    }
  }
  restored <- sub(colors[[gid]], original[[i]], restored, fixed = TRUE)
}
stopifnot(summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))
stopifnot(lengths(gregexpr("<svg ", baseline$svg, fixed = TRUE)) == 1L,
          identical(restored, baseline$svg),
          identical(undone, baseline$svg))

axis_titles <- names(labels)[2:5]
faces <- setNames(rep(list(list(weight = "bold", style = "italic")), 4L), axis_titles)
faced_svg <- adapter$render_ggplot(baseline$state, faces)
faced <- adapter$ggplot_manifest(baseline$state, "baseline", faces)$elements
for (gid in axis_titles) {
  item <- Filter(function(el) identical(el$gid, gid), faced)[[1L]]
  target <- baseline$state$text_targets[[gid]]
  line <- grep(paste0(">", target$label, "</text>"),
               strsplit(faced_svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
  stopifnot(length(line) == 1L, grepl("font-weight: bold", line, fixed = TRUE),
            grepl("font-style: italic", line, fixed = TRUE),
            !identical(item$bbox, as.list(target$bbox)),
            identical(item$editable[[2L]]$value, "bold"),
            identical(item$editable[[3L]]$value_original, "normal"))
}
axis_variant <- function(face) {
  svg <- adapter$render_ggplot(baseline$state, list("axis.x.bottom.title" = face))
  grep(">Engine displacement</text>", strsplit(svg, "\n")[[1L]], value = TRUE, fixed = TRUE)
}
stopifnot(grepl("font-weight: bold", axis_variant(list(weight = "bold")), fixed = TRUE),
          !grepl("font-style: italic", axis_variant(list(weight = "bold")), fixed = TRUE),
          grepl("font-style: italic", axis_variant(list(style = "italic")), fixed = TRUE),
          !grepl("font-weight: bold", axis_variant(list(style = "italic")), fixed = TRUE),
          identical(adapter$render_ggplot(baseline$state), baseline$svg),
          summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))

sizes <- setNames(rep(list(list(fontsize = 21.25)), length(axis_titles)), axis_titles)
sized_svg <- adapter$render_ggplot(baseline$state, sizes)
sized <- adapter$ggplot_manifest(baseline$state, "baseline", sizes)$elements
for (gid in axis_titles) {
  item <- Filter(function(el) identical(el$gid, gid), sized)[[1L]]
  original <- baseline$state$text_targets[[gid]]
  size <- Filter(function(field) identical(field$prop, "fontsize"), item$editable)[[1L]]
  stopifnot(identical(size$value, 21.25), identical(size$value_original, original$paint$fontsize),
            prod(unlist(item$bbox[3:4])) > prod(original$bbox[3:4]))
  line <- grep(paste0(">", original$label, "</text>"), strsplit(sized_svg, "\n")[[1L]],
               value = TRUE, fixed = TRUE)
  stopifnot(length(line) == 1L, grepl("font-size: 21.25px", line, fixed = TRUE))
}
stopifnot(identical(adapter$render_ggplot(baseline$state), baseline$svg),
          summary_calls == count_at_capture, identical(.Random.seed, rng_at_capture))

bad_value <- tryCatch(render_colors(baseline$state, list("plot.title" = "not-a-colour")),
                      error = conditionMessage)
stopifnot(identical(bad_value, "The native R text color must be an RGB hex color."))
limited <- adapter$capture_ggplot(
  plot + ggplot2::labs(title = NULL, x = expression(alpha), colour = "Shared", shape = "Shared") +
    ggplot2::theme(axis.title.y.left = ggplot2::element_blank()),
  c(432, 288))
stopifnot(identical(names(limited$state$text_targets),
                    c("axis.x.top.title", "axis.y.right.title", names(facets))),
          length(grep(">Shared</text>", strsplit(limited$svg, "\n")[[1L]], fixed = TRUE)) == 2L)
bad_target <- tryCatch(render_colors(limited$state, colors),
                       error = conditionMessage)
stopifnot(identical(bad_target, "The requested native R text is unavailable."),
          identical(grDevices::dev.cur(), c("null device" = 1L)))

hidden <- adapter$capture_ggplot(plot + ggplot2::guides(
  colour = ggplot2::guide_legend(title = expression(mu)),
  shape = ggplot2::guide_legend(title = NULL)
), c(432, 288))
stopifnot(identical(names(hidden$state$text_targets), c(head(names(labels), 5L), names(facets))))

secondary_plot <- plot +
  ggplot2::labs(subtitle = "Comparing drive configurations", caption = "Source: ggplot2 mpg") +
  ggplot2::theme(plot.subtitle = ggplot2::element_text(hjust = .5, colour = "#635479"),
                 plot.caption = ggplot2::element_text(hjust = 1, colour = "#426154"))
secondary <- adapter$capture_ggplot(secondary_plot, c(432, 288))
count_at_capture <- summary_calls
rng_at_capture <- .Random.seed
secondary_edits <- list("plot.subtitle" = "#B04964", "plot.caption" = "#2B7984")
edited <- render_colors(secondary$state, secondary_edits)
restored <- edited
anchors <- c("plot.subtitle" = .5, "plot.caption" = 1)
for (gid in names(secondary_edits)) {
  target <- secondary$state$text_targets[[gid]]
  stopifnot(identical(target$role, "text"))
  label <- paste0(">", target$label, "</text>")
  before <- grep(label, strsplit(secondary$svg, "\n")[[1L]], fixed = TRUE, value = TRUE)
  after <- grep(label, strsplit(edited, "\n")[[1L]], fixed = TRUE, value = TRUE)
  stopifnot(length(before) == 1L, grepl(target$color, before, fixed = TRUE),
            identical(after, sub(target$color, secondary_edits[[gid]], before, fixed = TRUE)))
  bbox <- target$bbox * c(432, 288, 432, 288)
  svg_x <- as.numeric(sub(".* x='([^']+)'.*", "\\1", after))
  stopifnot(abs(svg_x - bbox[[1L]] - bbox[[3L]] * anchors[[gid]]) < 1)
  restored <- sub(secondary_edits[[gid]], target$color, restored, fixed = TRUE)
}
stopifnot(identical(restored, secondary$svg),
          identical(render_colors(secondary$state), secondary$svg),
          count_at_capture == summary_calls, identical(rng_at_capture, .Random.seed))
unsupported_secondary <- adapter$capture_ggplot(
  secondary_plot + ggplot2::labs(subtitle = expression(alpha)) +
    ggplot2::theme(plot.caption = ggplot2::element_blank()), c(432, 288))
stopifnot(!any(names(secondary_edits) %in% names(unsupported_secondary$state$text_targets)))

ticks_plot <- ggplot2::ggplot(data.frame(x = 1:3, y = 1:3), ggplot2::aes(x, y)) +
  ggplot2::geom_point() +
  ggplot2::scale_x_continuous(breaks = 1:3, labels = c("Jan", "Feb", "Mar"),
    sec.axis = ggplot2::dup_axis(labels = c("First", "Second", "Third"))) +
  ggplot2::scale_y_continuous(breaks = 1:3, sec.axis = ggplot2::dup_axis()) +
  ggplot2::theme(axis.text.x.bottom = ggplot2::element_text(colour = "#314259", angle = 30, hjust = 1),
    axis.text.x.top = ggplot2::element_text(colour = "#674284"),
    axis.text.y.left = ggplot2::element_text(colour = "#735829"),
    axis.text.y.right = ggplot2::element_text(colour = "#245477"),
    axis.ticks = ggplot2::element_line(colour = "#050829"))
axis_capture <- function(plot) adapter$capture_ggplot(plot, c(432, 288))
tick_state <- axis_capture(ticks_plot)
tick_edits <- list("axis.x.bottom.ticks" = "#A64184", "axis.x.top.ticks" = "#A12529",
                   "axis.y.left.ticks" = "#834038", "axis.y.right.ticks" = "#254F29")
tick_text <- list(c("Jan", "Feb", "Mar"), c("First", "Second", "Third"), c("1", "2", "3"), c("1", "2", "3"))
stopifnot(all(names(tick_edits) %in% names(tick_state$state$text_targets)))
tick_svg <- render_colors(tick_state$state, tick_edits)
restored <- tick_svg
for (index in seq_along(tick_edits)) {
  gid <- names(tick_edits)[[index]]
  target <- tick_state$state$text_targets[[gid]]
  lines <- grep(paste0("fill: ", tick_edits[[gid]]), strsplit(tick_svg, "\n")[[1L]],
                 fixed = TRUE, value = TRUE)
  stopifnot(length(lines) == 3L, all(startsWith(lines, "<text ")),
            identical(sub("^.*>([^<]*)</text>$", "\\1", lines), tick_text[[index]]),
            identical(unlist(target$tick_labels), tick_text[[index]]))
  bbox <- target$bbox * c(432, 288, 432, 288)
  if (index == 1L) {
    xy <- sub("^.*translate\\(([^)]+)\\).*", "\\1", lines)
    anchors <- do.call(rbind, lapply(strsplit(xy, ","), as.numeric))
  } else {
    anchors <- cbind(as.numeric(sub(".* x='([^']+)'.*", "\\1", lines)),
                     as.numeric(sub(".* y='([^']+)'.*", "\\1", lines)))
  }
  stopifnot(all(anchors[, 1] >= bbox[1] - 1 & anchors[, 1] <= bbox[1] + bbox[3] + 1),
            all(anchors[, 2] >= bbox[2] - 1 & anchors[, 2] <= bbox[2] + bbox[4] + 1))
  restored <- gsub(tick_edits[[gid]], target$color, restored, fixed = TRUE)
}
stopifnot(identical(restored, tick_state$svg),
          identical(render_colors(tick_state$state), tick_state$svg))
single_axis <- render_colors(tick_state$state, tick_edits[1L])
stopifnot(identical(gsub(tick_edits[[1L]], "#314259", single_axis, fixed = TRUE), tick_state$svg))
for (guide in list(ggplot2::guide_axis(n.dodge = 2L), ggplot2::guide_axis(check.overlap = TRUE))) {
  irregular <- axis_capture(suppressWarnings(ticks_plot + ggplot2::guides(x = guide)))
  stopifnot(is.null(irregular$state$text_targets[["axis.x.bottom.ticks"]]))
}
formula_ticks <- axis_capture(suppressWarnings(ticks_plot +
  ggplot2::scale_x_continuous(breaks = 1:3, labels = expression(alpha, beta, gamma))))
stopifnot(is.null(formula_ticks$state$text_targets[["axis.x.bottom.ticks"]]))
stopifnot(!any(names(tick_edits) %in% names(baseline$state$text_targets)))

bar <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = cty)) +
  ggplot2::geom_point() + ggplot2::labs(colour = "City mileage") +
  ggplot2::scale_colour_continuous(breaks = 20) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom"))
bar_state <- adapter$capture_ggplot(bar, c(432, 288))$state
gid <- "legend.bottom.1.title"
stopifnot(identical(bar_state$text_targets[[gid]]$label, "City mileage"),
          !length(entries(bar_state)))
bar_edited <- render_colors(bar_state, setNames(list("#26838F"), gid))
bar_title <- grep(">City mileage</text>", strsplit(bar_edited, "\n")[[1L]],
                  value = TRUE, fixed = TRUE)
stopifnot(length(bar_title) == 1L, grepl("#26838F", bar_title, fixed = TRUE))

bar_data <- data.frame(x = 1:6, y = 6:1, signal = 0:5, reference = seq(0, 50, 10))
bar_points <- ggplot2::ggplot(bar_data, ggplot2::aes(x, y, colour = signal, fill = reference)) +
  ggplot2::geom_point(shape = 21)
bars <- bar_points +
  ggplot2::scale_colour_continuous(name = "Signal", breaks = c(1, 3, 5),
    labels = c("Low", "Middle", "High")) +
  ggplot2::scale_fill_continuous(name = "Reference", breaks = c(10, 30, 50),
    labels = c("Ten", "Thirty", "Fifty")) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom", angle = 25,
    theme = ggplot2::theme(legend.text = ggplot2::element_text(colour = "#614270"))),
    fill = ggplot2::guide_colourbar(position = "right", display = "rectangles",
    theme = ggplot2::theme(legend.text = ggplot2::element_text(colour = "#345E77"))))
captured_bars <- adapter$capture_ggplot(bars, c(432, 288))
bar_ids <- c("legend.bottom.1.colourbar.ticks", "legend.right.1.colourbar.ticks")
bar_colours <- c("#C32146", "#187958")
bar_labels <- list(c("Low", "Middle", "High"), c("Ten", "Thirty", "Fifty"))
for (index in seq_along(bar_ids)) {
  id <- bar_ids[[index]]
  target <- captured_bars$state$text_targets[[id]]
  stopifnot(identical(target$legend_title, c("Signal", "Reference")[[index]]),
            identical(unlist(target$tick_labels), bar_labels[[index]]),
            all(target$bbox[3:4] > 0))
  color <- bar_colours[[index]]
  svg <- render_colors(captured_bars$state, setNames(list(color), id))
  lines <- grep(paste0("fill: ", color), strsplit(svg, "\n")[[1L]], fixed = TRUE, value = TRUE)
  stopifnot(all(startsWith(lines, "<text ")),
            identical(sub("^.*>([^<]*)</text>$", "\\1", lines), bar_labels[[index]]),
            identical(gsub(color, target$color, svg, fixed = TRUE), captured_bars$svg))
}
stopifnot(identical(render_colors(captured_bars$state), captured_bars$svg))
bar_ticks <- function(plot) {
  captured <- adapter$capture_ggplot(plot, c(432, 288))
  grep("\\.colourbar\\.ticks$", names(captured$state$text_targets), value = TRUE)
}
stepped <- suppressWarnings(bars + ggplot2::scale_colour_steps(name = "Signal") +
  ggplot2::guides(colour = ggplot2::guide_coloursteps(position = "bottom")))
stopifnot(identical(bar_ticks(stepped), bar_ids[[2L]]))
duplicate <- bars +
  ggplot2::guides(colour = ggplot2::guide_colourbar(position = "bottom", title = "Shared"),
                 fill = ggplot2::guide_colourbar(position = "right", title = "Shared"))
stopifnot(!length(bar_ticks(duplicate)))
unlabelled <- bars + ggplot2::guides(
  colour = ggplot2::guide_colourbar(position = "bottom", title = NULL),
  fill = ggplot2::guide_colourbar(position = "right", theme = ggplot2::theme(
    legend.text = ggplot2::element_blank())))
stopifnot(!length(bar_ticks(unlabelled)))
bar_formula <- suppressWarnings(bars + ggplot2::scale_colour_continuous(name = "Signal",
  breaks = c(1, 3, 5), labels = expression(alpha, beta, gamma)))
stopifnot(identical(bar_ticks(bar_formula), bar_ids[[2L]]))

drive_plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point()
repeated_legend <- drive_plot +
  ggplot2::scale_colour_discrete(name = "Groups", labels = c("Repeated", "Repeated", "Other"))
labels <- entries(adapter$capture_ggplot(repeated_legend, c(432, 288))$state)
stopifnot(length(labels) == 1L, identical(labels[[1L]]$label, "Other"))
separate_guides <- drive_plot + ggplot2::geom_point(ggplot2::aes(shape = factor(cyl))) +
  ggplot2::scale_colour_discrete(name = "Drive", labels = c("Shared entry", "Front", "Rear")) +
  ggplot2::scale_shape_discrete(name = "Cylinders", labels = c("Shared entry", "Five", "Six", "Eight")) +
  ggplot2::guides(colour = ggplot2::guide_legend(order = 1), shape = ggplot2::guide_legend(order = 2))
separate <- adapter$capture_ggplot(separate_guides, c(432, 288))
shared <- Filter(function(target) identical(target$label, "Shared entry"), entries(separate$state))
stopifnot(length(shared) == 2L, identical(shared[[1L]]$legend_title, "Drive"),
          identical(shared[[2L]]$legend_title, "Cylinders"))
separate_edited <- render_colors(separate$state, setNames(list("#934896"), names(shared)[[2L]]))
shared_lines <- function(svg) grep(">Shared entry</text>", strsplit(svg, "\n")[[1L]],
                                   fixed = TRUE, value = TRUE)
stopifnot(identical(shared_lines(separate_edited)[[1L]], shared_lines(separate$svg)[[1L]]),
          grepl("#934896", shared_lines(separate_edited)[[2L]], fixed = TRUE))
wrap <- drive_plot + ggplot2::labs(colour = "Drive type") +
  ggplot2::guides(colour = ggplot2::guide_legend(ncol = 2, position = "bottom"))
wrapped <- adapter$capture_ggplot(wrap, c(432, 288))
bottom_entries <- entries(wrapped$state)
stopifnot(length(bottom_entries) == 3L, all(startsWith(names(bottom_entries), "legend.bottom.")))
bottom_gid <- names(bottom_entries)[[3L]]
bottom_label <- paste0(">", bottom_entries[[3L]]$label, "</text>")
wrapped_edited <- render_colors(wrapped$state, setNames(list("#6F2A9B"), bottom_gid))
wrapped_line <- grep(bottom_label, strsplit(wrapped_edited, "\n")[[1L]], fixed = TRUE, value = TRUE)
stopifnot(length(wrapped_line) == 1L, grepl("#6F2A9B", wrapped_line, fixed = TRUE))

for (side in c("left", "top", "inside")) {
  positioned <- drive_plot + ggplot2::theme(legend.position = side, legend.position.inside = c(.7, .8))
  captured <- adapter$capture_ggplot(positioned, c(432, 288))
  gid <- paste0("legend.", side)
  target <- captured$state$legend_targets[[gid]]
  stopifnot(!is.null(target), grepl(paste0("id='", gid, "'"), captured$svg, fixed = TRUE))
  edits <- setNames(list(list(loc_frac = c(.37, .68))), gid)
  manifest <- adapter$ggplot_manifest(captured$state, "guide-test", edits)
  moved <- Filter(function(element) element$gid == gid, manifest$elements)[[1L]]
  stopifnot(isTRUE(all.equal(unlist(moved$anchor), c(.37, .68))),
            identical(captured$svg, adapter$render_ggplot(captured$state)))
}
# A single outside box owns both guides that ggplot2 placed on that side.
group_edits <- list("legend.right" = list(loc_frac = c(.32, .71)))
two_guides <- adapter$ggplot_manifest(separate$state, "guide-test", group_edits)$elements
shared_titles <- Filter(function(element) identical(element$label, "Shared entry"), two_guides)
stopifnot(length(shared_titles) == 2L,
          all(vapply(shared_titles, function(element) element$drag_host == "legend.right", logical(1L))))

data <- data.frame(x = 1:4, y = 4:1, row = rep(c("Sample", "Shared"), each = 2),
                   column = rep(c("Control", "Shared"), 2))
points <- ggplot2::ggplot(data, ggplot2::aes(x, y)) + ggplot2::geom_point() +
  ggplot2::theme(strip.text.x = ggplot2::element_text(colour = "#375994"),
                 strip.text.y = ggplot2::element_text(colour = "#4F7152"))
capture <- function(plot) adapter$capture_ggplot(plot, c(432, 288))
facet_targets <- function(plot) {
  targets <- capture(plot)$state$text_targets
  targets[startsWith(names(targets), "facet.")]
}
grid <- capture(points + ggplot2::facet_grid(ggplot2::vars(row), ggplot2::vars(column)))
left <- facet_targets(points + ggplot2::facet_wrap(ggplot2::vars(row), strip.position = "left"))
bottom <- facet_targets(points + ggplot2::facet_wrap(ggplot2::vars(column), strip.position = "bottom"))
stopifnot(identical(names(left), c("facet.l.1.2.label", "facet.l.1.1.label")),
          identical(names(bottom), c("facet.b.1.1.label", "facet.b.2.1.label")))
across <- grid$state$text_targets[["facet.t.2.label"]]
down <- grid$state$text_targets[["facet.r.2.label"]]
stopifnot(across$label == "Shared", down$label == "Shared",
          across$color == "#375994", down$color == "#4F7152")
grid_edit <- render_colors(grid$state, list("facet.r.2.label" = "#BC4964"))
row <- grep("#BC4964", strsplit(grid_edit, "\n")[[1L]], value = TRUE, fixed = TRUE)
bbox <- down$bbox * c(432, 288, 432, 288)
anchor <- as.numeric(strsplit(sub(".*translate\\(([^)]+)\\).*", "\\1", row), ",")[[1L]])
stopifnot(length(row) == 1L, grepl(">Shared</text>", row, fixed = TRUE),
          abs(anchor[[2L]] - bbox[[2L]] - bbox[[4L]] / 2) < 1,
          identical(sub("#BC4964", "#4F7152", grid_edit, fixed = TRUE), grid$svg))

repeat_labels <- ggplot2::labeller(column = function(values) rep("Repeated facet", length(values)))
repeated <- capture(points + ggplot2::facet_wrap(ggplot2::vars(column), labeller = repeat_labels))
stacked <- points + ggplot2::facet_wrap(ggplot2::vars(row, column))
formula <- points + ggplot2::facet_wrap(ggplot2::vars(column), labeller = ggplot2::label_parsed)
blank <- points + ggplot2::facet_wrap(ggplot2::vars(column)) +
  ggplot2::theme(strip.text.x = ggplot2::element_blank())
stopifnot(!any(startsWith(names(repeated$state$text_targets), "facet.")),
          length(grep(">Repeated facet</text>", strsplit(repeated$svg, "\n")[[1L]],
                      fixed = TRUE)) == 2L,
          !length(facet_targets(stacked)), !length(facet_targets(formula)),
          !length(facet_targets(blank)))

if (capabilities("cairo")) {
  cairo_pdf <- grDevices::cairo_pdf
  utils::assignInNamespace("cairo_pdf", function(...) warning("fixture missing Cairo"), "grDevices")
  failed_pdf <- suppressWarnings(tryCatch(
    adapter$export_ggplot_pdf(baseline$state, color_edits(colors), "unavailable.pdf"),
    error = conditionMessage
  ))
  utils::assignInNamespace("cairo_pdf", cairo_pdf, "grDevices")
  stopifnot(grepl("fixture missing Cairo", failed_pdf, fixed = TRUE),
            !file.exists("unavailable.pdf"), !file.exists("Rplots.pdf"),
            identical(grDevices::dev.cur(), c("null device" = 1L)))
}
"""

PRECISION_PROBE = r"""
adapter <- new.env(parent = baseenv())
sys.source(commandArgs(trailingOnly = TRUE)[[1L]], envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_points.R"), envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_bars.R"), envir = adapter)
sys.source(file.path(dirname(commandArgs(trailingOnly = TRUE)[[1L]]), "ggplot_ribbons.R"), envir = adapter)
if (!requireNamespace("ggplot2", quietly = TRUE) ||
    !requireNamespace("svglite", quietly = TRUE)) quit(status = 77L)

values <- data.frame(x = c(0, 2, 4, 6), y = c(1, 4, 3, 5), colour = c(-12, -4, 4, 12))
summary_calls <- 0L
tracked_mean <- function(v) {
  summary_calls <<- summary_calls + 1L
  mean(v)
}
plot <- ggplot2::ggplot(values, ggplot2::aes(x, y)) +
  ggplot2::geom_line(colour = "#123456", alpha = .6) +
  ggplot2::geom_point(ggplot2::aes(colour = colour)) +
  ggplot2::stat_summary(fun = tracked_mean, geom = "point", shape = 4) +
  ggplot2::scale_colour_gradient(name = "Direction", breaks = c(-12, 0, 12)) +
  ggplot2::scale_x_continuous(breaks = c(0, 2, 4, 6))
frame <- c(432, 288)
ticks <- "axis.x.bottom.ticks"
bar <- "legend.right.1.colourbar.ticks"
curve <- "layer.1.line"
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
stopifnot(identical(state$tick_targets[[bar]]$values, c(-12, 0, 12)),
          identical(names(state$line_targets), curve))
pre_calls <- summary_calls

edits <- list()
edits[[ticks]] <- list(format = "%.2f", major_mode = "step", major_step = 1.5, fontsize = 18)
edits[[bar]] <- list(format = "%.1f")
edits[[curve]] <- list(linewidth = 2.4, linestyle = "--", color = "#538E25")
svg <- adapter$render_ggplot(state, edits)
stopifnot(length(svg) == 1L, grepl(">1.50</text>", svg, fixed = TRUE),
          !grepl(">2.00</text>", svg, fixed = TRUE),
          grepl(">0.0</text>", svg, fixed = TRUE),
          grepl(">12.0</text>", svg, fixed = TRUE))

# Inspect the actually assembled native grid, curve and palette. Changing
# captions can resize the layout, so compare within their native viewports.
probe <- function(table) {
  panel <- table$grobs[[which(table$layout$name == "panel")]]
  grill <- panel$children[[grep("^grill", names(panel$children))]]
  major <- grill$children[[grep("^panel.grid.major.x", names(grill$children))]]
  line <- panel$children[[state$line_targets[[curve]]$child]]
  points <- panel$children[[grep("^geom_point.points", names(panel$children))[[1L]]]]
  box <- table$grobs[[which(table$layout$name == "guide-box-right")]]
  guide <- box$grobs[[which(box$layout$name == "guides")]]
  bar <- guide$grobs[[which(guide$layout$name == "bar")]]
  label <- guide$grobs[[grep("^label", guide$layout$name)]]$children[[1L]]
  tick_source <- state$text_targets[[ticks]]
  tick_paint <- adapter$ggplot_text_target(table, tick_source$slots, "ticks",
                                          group_label = tick_source$label)$paint
  list(grid_x = unique(as.numeric(major$x)), line = line, points = points,
       raster = bar$raster, label_y = as.numeric(label$y), tick_size = tick_paint$fontsize)
}
before <- probe(state$table)
after <- adapter$with_svg_frame(frame, function(capture) {
  grid::grid.newpage()
  probe(adapter$ggplot_table_for(state, edits)$table)
})$value
view <- state$built$layout$panel_params[[1L]]$x
stopifnot(isTRUE(all.equal(after$grid_x, view$rescale(seq(0, 6, by = 1.5)))),
          length(before$grid_x) == 4L,
          after$tick_size == 18, before$tick_size != after$tick_size,
          identical(after$line$x, before$line$x), identical(after$line$y, before$line$y),
          identical(after$points, before$points),
          identical(after$raster, before$raster),
          identical(after$label_y, before$label_y),
          after$line$gp$col == "#538E2599", after$line$gp$lty == "dashed",
          after$line$gp$lwd * 72 / 96 == 2.4)
stopifnot(identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, edits), svg),
          identical(adapter$render_ggplot(state, list(
            "axis.x.bottom.ticks" = list(major_mode = "auto", major_step = 1.5))), captured$svg),
          summary_calls == pre_calls)

for (invalid in list(list(major_step = 0), list(major_step = 1e-300),
                     list(major_mode = "fixed"), list(format = "%n"), list(fontsize = 0))) {
  bad <- stats::setNames(list(invalid), ticks)
  stopifnot(inherits(try(adapter$render_ggplot(state, bad), silent = TRUE), "try-error"))
}
stopifnot(inherits(try(adapter$render_ggplot(state, stats::setNames(
  list(list(linewidth = -1)), curve)), silent = TRUE), "try-error"))
custom <- adapter$capture_ggplot(plot +
  ggplot2::scale_x_continuous(breaks = c(0, 2, 4, 6), labels = c("Zero", "Two", "Four", "Six")),
  frame)
stopifnot(is.null(custom$state$tick_targets[[ticks]]),
          !is.null(custom$state$text_targets[[ticks]]))
secondary <- adapter$capture_ggplot(plot +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis()), frame)
stopifnot(is.null(secondary$state$tick_targets[[ticks]]$spacing))
"""

COLOR_SCALE_PROBE = r"""
adapter <- new.env(parent = baseenv())
adapter_file <- commandArgs(trailingOnly = TRUE)[[1L]]
sys.source(adapter_file, envir = adapter)
sys.source(file.path(dirname(adapter_file), "ggplot_colour_scale.R"), envir = adapter)
sys.source(file.path(dirname(adapter_file), "ggplot_points.R"), envir = adapter)
sys.source(file.path(dirname(adapter_file), "ggplot_bars.R"), envir = adapter)
sys.source(file.path(dirname(adapter_file), "ggplot_ribbons.R"), envir = adapter)
if (!all(vapply(c("ggplot2", "svglite", "jsonlite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
palettes <- jsonlite::fromJSON(
  file.path(dirname(dirname(adapter_file)), "color_palettes.json"), simplifyVector = FALSE)

data <- data.frame(x = 1:4, y = c(2, 4, 1, 3), z = c(0, 2, 4, 6), tile = c(8, 2, 5, 4))
computations <- 0L
tracked <- function(x) {
  computations <<- computations + 1L
  mean(x)
}
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_tile(ggplot2::aes(fill = tile), width = .12, height = .12) +
  ggplot2::geom_point(ggplot2::aes(colour = z)) +
  ggplot2::geom_point(ggplot2::aes(y = y + .1, colour = z), shape = 15) +
  ggplot2::stat_summary(fun = tracked, geom = "point", shape = 4) +
  ggplot2::scale_colour_gradient(name = "Exposure", guide = ggplot2::guide_colourbar(position = "right")) +
  ggplot2::scale_fill_gradient(name = "Independent", guide = ggplot2::guide_colourbar(position = "left"))
captured <- adapter$capture_ggplot(plot, c(432, 288), palettes)
state <- captured$state
gid <- "legend.right.1.colourbar"
other <- "legend.left.1.colourbar"
target <- state$colour_scales[[gid]]
stopifnot(identical(target$limits, c(0, 6)), identical(names(target$members), c("2", "3")),
          identical(names(state$colour_scales[[other]]$members), "1"))
calls <- computations
scale_edit <- list(vmin = 1, vmax = 5, cmap = "viridis")
edits <- stats::setNames(list(scale_edit, list(format = "%.1f")), c(gid, paste0(gid, ".ticks")))
result <- adapter$ggplot_remap_colour_scales(state, edits[gid], state$built$plot$guides$params)
upper <- "#FDE725"
lower <- "#440154"
expected <- tolower(c(lower, "#3B528B", "#5EC962", upper))
for (idx in c(2L, 3L)) {
  stopifnot(identical(result$data[[idx]]$colour, expected),
            identical(result$data[[idx]][c("x", "y")], state$layers[[idx]][c("x", "y")]))
}
stopifnot(identical(result$data[[1L]], state$layers[[1L]]),
          identical(result$data[[4L]], state$layers[[4L]]))
decor <- result$guides[[target$index]]$decor
stopifnot(identical(decor$value[c(1L, nrow(decor))], c(1, 5)),
          identical(toupper(decor$colour[c(1L, nrow(decor))]), c(lower, upper)))
svg <- adapter$render_ggplot(state, edits)
stopifnot(grepl(paste0("fill: ", lower), svg, fixed = TRUE),
          grepl(paste0("fill: ", upper), svg, fixed = TRUE),
          grepl(">1.0</text>", svg, fixed = TRUE), grepl(">5.0</text>", svg, fixed = TRUE))
replay <- adapter$ggplot_manifest(state, "test", edits)$elements
element <- Filter(function(el) el$gid == gid, replay)[[1L]]
stopifnot(identical(element$color_scale$limits, as.list(c(1, 5))),
          identical(element$color_scale$cmap, "viridis"),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, edits), svg),
          computations == calls)

# The same saved document can style the series and its colour scale together.
point_gid <- "layer.2.scatter"
stopifnot(identical(names(state$point_targets[[point_gid]]$values),
                    c("size", "marker", "alpha")))
point_edit <- list(size = 64, marker = "s", alpha = .5)
mixed <- c(edits, stats::setNames(list(point_edit), point_gid))
scene <- adapter$with_svg_frame(state$frame_pt, function(capture) {
  adapter$ggplot_table_for(state, mixed)
})$value
target <- state$point_targets[[point_gid]]
baseline_points <- adapter$ggplot_point_grob(state$table$grobs[[target$slot]], target)
actual_points <- adapter$ggplot_point_grob(scene$table$grobs[[target$slot]], target)
sibling <- state$point_targets[["layer.3.scatter"]]
sibling_points <- adapter$ggplot_point_grob(scene$table$grobs[[sibling$slot]], sibling)
stopifnot(identical(actual_points$x, baseline_points$x),
          identical(actual_points$y, baseline_points$y),
          identical(actual_points$pch, 15L),
          all(abs(actual_points$gp$fontsize - actual_points$gp$lwd - 8) < 1e-10),
          identical(tolower(actual_points$gp$col), paste0(expected, "80")),
          identical(tolower(sibling_points$gp$col), expected))
point_elements <- adapter$ggplot_manifest(state, "test", mixed)$elements
point <- Filter(function(el) el$gid == point_gid, point_elements)[[1L]]
stopifnot(identical(point$point_series$current, point_edit),
          !identical(point$bbox, target$bbox),
          identical(adapter$render_ggplot(state), captured$svg),
          computations == calls)
point_svg <- adapter$render_ggplot(state, mixed)
stopifnot(grepl("fill: #440154; fill-opacity: 0.50", point_svg, fixed = TRUE),
          !identical(point_svg, svg))
bad_point <- function(patch) stats::setNames(list(patch), point_gid)
for (bad in list(list(size = 0), list(marker = "unknown"), list(alpha = -1))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, bad_point(bad)), silent = TRUE), "try-error"))
}
mapped <- adapter$capture_ggplot(ggplot2::ggplot(data, ggplot2::aes(x, y, size = z)) +
  ggplot2::geom_point(show.legend = FALSE), c(432, 288), palettes)$state
mapped_points <- mapped$point_targets[["layer.1.scatter"]]
stopifnot(is.null(mapped_points$values$size), !is.null(mapped_points$values$marker))

for (bad in list(list(vmin = 6, vmax = 6), list(vmin = 10), list(vmax = Inf),
                 list(cmap = "invented"))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, stats::setNames(list(bad), gid)),
                        silent = TRUE), "try-error"))
}
stopifnot(identical(adapter$render_ggplot(state), captured$svg))
unstable <- plot + ggplot2::geom_point(ggplot2::aes(colour = z, alpha = ggplot2::after_scale(alpha)))
stopifnot(is.null(adapter$capture_ggplot(unstable, c(432, 288), palettes)$state$colour_scales[[gid]]))

paired_plot <- ggplot2::ggplot(data, ggplot2::aes(x, y, colour = z, fill = z)) +
  ggplot2::geom_point(shape = 21) +
  ggplot2::scale_colour_gradient(aesthetics = c("colour", "fill")) +
  ggplot2::guides(colour = ggplot2::guide_colourbar(title = "Edge", position = "right"),
                 fill = ggplot2::guide_colourbar(title = "Interior", position = "left"))
paired <- adapter$capture_ggplot(paired_plot, c(432, 288), palettes)$state
stopifnot(length(paired$colour_scales) == 1L, length(paired$colour_scales[[1L]]$guides) == 2L)
paired_edits <- stats::setNames(list(scale_edit), names(paired$colour_scales))
paired_result <- adapter$ggplot_remap_colour_scales(paired, paired_edits, paired$built$plot$guides$params)
stopifnot(identical(paired_result$data[[1L]]$colour, expected),
          identical(paired_result$data[[1L]]$fill, expected),
          all(vapply(paired_result$guides, function(guide) {
            identical(toupper(guide$decor$colour[c(1L, nrow(guide$decor))]), c(lower, upper))
          }, logical(1L))))
stopifnot(length(adapter$render_ggplot(paired, paired_edits)) == 1L)
"""


BAR_PROBE = r"""
adapter <- new.env(parent = baseenv())
native <- dirname(commandArgs(trailingOnly = TRUE)[[1L]])
for (file in c("ggplot2.R", "ggplot_colour_scale.R", "ggplot_points.R", "ggplot_bars.R", "ggplot_ribbons.R")) {
  sys.source(file.path(native, file), envir = adapter)
}
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
frame <- c(216, 144)
data <- data.frame(cat = c("One", "Three", "Two", "Three"), height = c(4, -1, 2, -3),
                   group = c("control", "control", "control", "other"))
runs <- 0L
observed <- function(data) { runs <<- runs + 1L; data }
plot <- ggplot2::ggplot(data, ggplot2::aes(cat)) +
  ggplot2::geom_col(ggplot2::aes(y = height, group = group), data = observed,
                    fill = "#226688", colour = "#101112", linewidth = .6) +
  ggplot2::geom_bar(fill = "#77889A", width = .3)
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
first <- "layer.1.bars"
second <- "layer.2.bars"
stopifnot(identical(names(state$bar_targets), c(first, second)), runs == 1L)
stopifnot(identical(sort(state$layers[[2L]]$count), c(1, 1, 2)))
element <- function(manifest, gid) Filter(function(e) e$gid == gid, manifest$elements)[[1L]]
original <- adapter$ggplot_manifest(state, "bars")
baseline <- element(original, first)
stopifnot(baseline$role == "bar_series", length(baseline$geometry$paths) == 4L,
          length(element(original, second)$geometry$paths) == 3L)
patch <- setNames(list(list(facecolor = "#E25832", edgecolor = "#663399", linewidth = 2.2,
                            alpha = .75)), first)
scene <- adapter$ggplot_table_for(state, patch)
target <- state$bar_targets[[first]]
before <- adapter$ggplot_bar_rect(state$table$grobs[[target$slot]], target)
after <- adapter$ggplot_bar_rect(scene$table$grobs[[target$slot]], target)
stopifnot(identical(before[c("x", "y", "width", "height")], after[c("x", "y", "width", "height")]),
          all(abs(after$gp$lwd * adapter$ggplot_lwd_pt - 2.2) < 1e-9),
          identical(toupper(after$gp$fill), "#E25832BF"),
          identical(toupper(after$gp$col), "#663399BF"))
edited_svg <- adapter$render_ggplot(state, patch)
stopifnot(grepl("fill: #E25832", edited_svg, fixed = TRUE),
          grepl("stroke: #663399", edited_svg, fixed = TRUE),
          grepl("stroke-width: 2.20", edited_svg, fixed = TRUE),
          grepl("fill-opacity: 0.75", edited_svg, fixed = TRUE))
edited <- adapter$ggplot_manifest(state, "bars", patch)
stopifnot(identical(element(edited, first)$geometry$paths, baseline$geometry$paths),
          identical(element(edited, second), element(original, second)),
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, patch), edited_svg), runs == 1L)
bad <- list(facecolor = c(rgb(1, 0, 0), rgb(0, 1, 0)), edgecolor = "yellow",
            linewidth = 5.1, alpha = -.1, bar_width = .4)
for (prop in names(bad)) {
  stopifnot(inherits(try(adapter$render_ggplot(state, setNames(list(bad[prop]), first)),
                        silent = TRUE), "try-error"))
}
boundaries <- setNames(list(list(edgecolor = "none", linewidth = 0, alpha = 0)), first)
invisible_bar <- element(adapter$ggplot_manifest(state, "bars", boundaries), first)$geometry
stopifnot(length(adapter$render_ggplot(state, boundaries)) == 1L,
          !invisible_bar$fill, is.null(invisible_bar$fill_alpha))
horizontal <- ggplot2::ggplot(data, ggplot2::aes(height, cat)) +
  ggplot2::geom_col(fill = "#226688", linetype = 0)
h <- adapter$capture_ggplot(horizontal, frame)$state
stopifnot(identical(names(h$bar_targets[[first]]$values), c("facecolor", "alpha")),
          grepl("fill-opacity: 0.75", adapter$render_ggplot(h, setNames(list(list(alpha = .75)), first)),
                fixed = TRUE))
mapped <- ggplot2::ggplot(data, ggplot2::aes(cat, height, fill = group)) + ggplot2::geom_col()
stopifnot(length(adapter$capture_ggplot(mapped, frame)$state$bar_targets) == 0L,
          length(adapter$capture_ggplot(mapped + ggplot2::guides(fill = "none"), frame)$state$bar_targets) == 0L,
          length(adapter$capture_ggplot(plot + ggplot2::facet_wrap(ggplot2::vars(group)), frame)$state$bar_targets) == 0L)
many <- ggplot2::ggplot(data.frame(x = 1:501, y = rep(1, 501)), ggplot2::aes(x, y)) +
  ggplot2::geom_col()
large <- adapter$capture_ggplot(many, frame)$state
stopifnot(is.null(large$bar_geometry[[first]]$geometry), length(large$bar_geometry[[first]]$bbox) == 4L)
"""

ERRORBAR_PROBE = r"""
adapter <- new.env(parent = baseenv())
native <- dirname(commandArgs(trailingOnly = TRUE)[[1L]])
for (file in c("ggplot2.R", "ggplot_colour_scale.R", "ggplot_points.R", "ggplot_bars.R", "ggplot_ribbons.R")) {
  sys.source(file.path(native, file), envir = adapter)
}
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
data <- data.frame(x = c(1, 2), y = c(3, 4), lower = c(2, 2.5), upper = c(5, 6))
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
plot <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_errorbar(ggplot2::aes(ymin = lower, ymax = upper), data = observed,
                         linewidth = .5, colour = "#123456", width = .3) +
  ggplot2::geom_point() + ggplot2::labs(y = "Diffusion (mm)")
frame <- c(216, 144)
captured <- adapter$capture_ggplot(plot, frame)
state <- captured$state
gid <- "layer.1.errorbar"
element <- function(edits = list()) {
  Filter(function(e) e$gid == gid, adapter$ggplot_manifest(state, "interval", edits)$elements)[[1L]]
}
original <- element()
stopifnot(original$role == "errorbar", length(original$geometry$paths) == 6L,
          all(lengths(lapply(original$geometry$paths, `[[`, "points")) == 2L))
style <- list(color = "#8A2530", linewidth = 2.1, alpha = .6)
patch <- setNames(list(style), gid)
svg <- adapter$render_ggplot(state, patch)
stopifnot(identical(element(patch)$geometry$paths, original$geometry$paths),
          identical(element(patch)$errorbar_series$current, style),
          any(grepl("stroke: #8A2530", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE) &
              grepl("stroke-width: 2.10", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE) &
              grepl("stroke-opacity: 0.60", strsplit(svg, "\n", fixed = TRUE)[[1L]], fixed = TRUE)),
          identical(adapter$render_ggplot(state), captured$svg), calls == 1L)
horizontal <- ggplot2::ggplot(data, ggplot2::aes(y, x)) +
  ggplot2::geom_errorbar(ggplot2::aes(xmin = lower, xmax = upper), width = .3)
stopifnot(length(adapter$capture_ggplot(horizontal, frame)$state$line_targets) == 1L)
mapped <- ggplot2::ggplot(data, ggplot2::aes(x, y)) +
  ggplot2::geom_errorbar(ggplot2::aes(ymin = lower, ymax = upper, colour = x), show.legend = FALSE)
stopifnot(length(adapter$capture_ggplot(mapped, frame)$state$line_targets) == 0L)
for (bad in list(list(alpha = 1.01), list(linewidth = 5.01), list(capsize = 2))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, setNames(list(bad), gid)),
                        silent = TRUE), "try-error"))
}

family_gid <- "axis.y.left.title"
font <- setNames(list(list(fontfamily = "monospace")), family_gid)
text <- function(edits) {
  Filter(function(e) e$gid == family_gid, adapter$ggplot_manifest(state, "interval", edits)$elements)[[1L]]
}
field <- Filter(function(e) e$prop == "fontfamily", text(font)$editable)[[1L]]
stopifnot(identical(field$value, "monospace"), identical(field$value_original, "sans-serif"),
          !identical(text(font)$bbox, text(list())$bbox),
          identical(adapter$ggplot_text_paint(state$text_targets[[family_gid]], font[[1L]])$paint$fontfamily,
                    unname(systemfonts::font_info(family = "mono")$family)),
          identical(adapter$render_ggplot(state), captured$svg), calls == 1L)
"""

RANGE_AND_GROUPED_LINE_PROBE = r"""
adapter <- new.env(parent = baseenv())
native <- dirname(commandArgs(trailingOnly = TRUE)[[1L]])
for (file in c("ggplot2.R", "ggplot_colour_scale.R", "ggplot_points.R", "ggplot_bars.R", "ggplot_ribbons.R")) {
  sys.source(file.path(native, file), envir = adapter)
}
if (!all(vapply(c("ggplot2", "svglite"), requireNamespace, logical(1L),
                quietly = TRUE))) quit(status = 77L)
d <- data.frame(x = rep(1:5, 2), y = c(2, 3, 2.7, 3.5, 4, 5, 5.2, 6, 5.9, 7),
                group = rep(c("Control", "Treated"), each = 5))
d$lower <- d$y - .2
d$upper <- d$y + .38
gapped <- d
gapped$y[3L] <- NA_real_
calls <- 0L
observed <- function(data) { calls <<- calls + 1L; data }
base <- ggplot2::ggplot(d, ggplot2::aes(x, y, group = group))
band <- ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper),
                             fill = "#559944", alpha = .4, data = observed)
line <- ggplot2::geom_line(ggplot2::aes(colour = group), data = gapped, linewidth = .7)
points <- ggplot2::geom_point(ggplot2::aes(colour = group))
palette <- ggplot2::scale_colour_manual(values = c(Control = "#2369A3", Treated = "#A53658"))
frame <- c(316, 212)
captured <- adapter$capture_ggplot(base + band + line + points + palette, frame)
state <- captured$state
range_id <- "layer.1.ribbon"
line_id <- "layer.2.line"
stopifnot(calls == 1L, length(state$line_targets[[line_id]]$guide_keys) == 1L)
element <- function(all, gid) Filter(function(e) e$gid == gid, all$elements)[[1L]]
baseline <- adapter$ggplot_manifest(state, "plot")
initial_line <- element(baseline, line_id)
initial_band <- element(baseline, range_id)
stopifnot(initial_line$geometry$kind == "multi_path",
          identical(unname(lengths(lapply(initial_line$geometry$paths, `[[`, "points"))), c(2L, 2L, 5L)),
          length(initial_band$geometry$paths) == 2L,
          all(vapply(initial_band$geometry$paths, `[[`, logical(1L), "closed")),
          initial_band$geometry$paint_order < initial_line$geometry$paint_order,
          !("color" %in% vapply(initial_line$editable, `[[`, character(1L), "prop")))
patches <- setNames(list(list(facecolor = "#695DB2", alpha = .6),
                         list(linewidth = 2.5, linestyle = "--")), c(range_id, line_id))
actual <- adapter$ggplot_manifest(state, "plot", patches)
svg <- adapter$render_ggplot(state, patches)
rows <- strsplit(svg, "\n", fixed = TRUE)[[1L]]
for (color in c("#2369A3", "#A53658")) {
  painted <- grepl(paste0("stroke: ", color), rows, fixed = TRUE)
  styled <- grepl("stroke-width: 2.50", rows, fixed = TRUE) &
    grepl("stroke-dasharray:", rows, fixed = TRUE)
  stopifnot(any(startsWith(rows, "<polyline ") & painted & styled),
            sum(startsWith(rows, "<line ") & painted & styled) == 1L,
            sum(startsWith(rows, "<circle ") & painted & !styled) == 6L)
}
stopifnot(sum(grepl("fill: #695DB2; fill-opacity: 0.60", rows, fixed = TRUE)) == 2L,
          identical(element(actual, range_id)$geometry$paths, initial_band$geometry$paths),
          identical(element(actual, line_id)$geometry$paths, initial_line$geometry$paths),
          element(actual, range_id)$geometry$paint_order < element(actual, line_id)$geometry$paint_order,
          identical(adapter$render_ggplot(state), captured$svg),
          identical(adapter$render_ggplot(state, patches), svg), calls == 1L)
covered <- adapter$capture_ggplot(base + line + band + palette, frame)$state
covered_manifest <- adapter$ggplot_manifest(covered, "plot")
stopifnot(element(covered_manifest, "layer.1.line")$geometry$paint_order <
          element(covered_manifest, "layer.2.ribbon")$geometry$paint_order)
outline <- base + ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper),
                                      fill = "#559944", colour = "#551144", alpha = .4)
outline_state <- adapter$capture_ggplot(outline, frame)$state
outline_svg <- adapter$render_ggplot(outline_state, setNames(list(list(facecolor = "#695DB2")), range_id))
stopifnot(is.null(outline_state$ribbon_targets[[range_id]]$values$alpha),
          grepl("stroke: #551144", outline_svg, fixed = TRUE),
          grepl("fill: #695DB2; fill-opacity: 0.40", outline_svg, fixed = TRUE))
area <- base + ggplot2::geom_area(fill = "#559944", alpha = .4)
stopifnot(length(adapter$capture_ggplot(area, frame)$state$ribbon_targets) == 1L)
mapped <- base + ggplot2::geom_ribbon(ggplot2::aes(ymin = lower, ymax = upper, fill = group))
stopifnot(length(adapter$capture_ggplot(mapped, frame)$state$ribbon_targets) == 0L)
duplicated_key <- base + line + line + palette
stopifnot(length(adapter$capture_ggplot(duplicated_key, frame)$state$line_targets) == 0L)
intentional_key <- base + line + palette +
  ggplot2::guides(colour = ggplot2::guide_legend(override.aes = list(linetype = 2)))
stopifnot(length(adapter$capture_ggplot(intentional_key, frame)$state$line_targets) == 0L)
for (edit in list(setNames(list(list(color = "#115500")), line_id),
                  setNames(list(list(alpha = 2)), range_id))) {
  stopifnot(inherits(try(adapter$render_ggplot(state, edit), silent = TRUE), "try-error"))
}
"""


def _assert_r_probe(tmp_path, program):
    rscript, explicit_rscript = _rscript()
    driver = tmp_path / "probe.R"
    driver.write_text(program, encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(driver), str(ADAPTER)],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=_COLD_RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode == 77 and not explicit_rscript:
        pytest.skip("Rscript does not have ggplot2 and svglite")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "Rplots.pdf").exists()


def test_ggplot_title_colors_keep_the_built_layers_and_replay(tmp_path):
    _assert_r_probe(tmp_path, PROBE)


def test_ggplot_tick_and_curve_edits_preserve_native_values_and_colours(tmp_path):
    _assert_r_probe(tmp_path, PRECISION_PROBE)


def test_ggplot_colour_scale_replays_all_mapped_layers_with_the_same_guide(tmp_path):
    _assert_r_probe(tmp_path, COLOR_SCALE_PROBE)


def test_ggplot_bar_paint_preserves_counts_positions_and_siblings(tmp_path):
    _assert_r_probe(tmp_path, BAR_PROBE)


def test_ggplot_errorbar_stroke_and_title_family_preserve_native_data(tmp_path):
    _assert_r_probe(tmp_path, ERRORBAR_PROBE)


def test_range_and_grouped_line_edits_preserve_geometry_and_sync_the_legend(tmp_path):
    _assert_r_probe(tmp_path, RANGE_AND_GROUPED_LINE_PROBE)


def test_owned_r_uses_the_native_registry_transport_and_lease(tmp_path):
    rscript, explicit_rscript = _rscript()
    available = subprocess.run(
        [
            rscript,
            "--vanilla",
            "-e",
            'quit(status = if (all(vapply(c("ggplot2", "svglite", "jsonlite"), '
            "requireNamespace, logical(1), quietly = TRUE))) 0L else 77L)",
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )
    if available.returncode == 77 and not explicit_rscript:
        pytest.skip("Rscript does not have the native adapter packages")
    assert available.returncode == 0, available.stdout + available.stderr
    script = tmp_path / "数据 图.R"
    script.write_text(
        """
stopifnot(!nzchar(Sys.getenv("TAVOTTO_BRIDGE_TOKEN")))
cat('{"bridge_event":"ended"}\n')
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = cty)) +
  ggplot2::geom_point() +
  ggplot2::labs(title = "Fuel economy", x = "Engine displacement", y = "Highway mileage") +
  ggplot2::scale_x_continuous(sec.axis = ggplot2::dup_axis()) +
  ggplot2::scale_colour_continuous(name = "City mileage", breaks = c(10, 20, 30),
    labels = c("Ten", "Twenty", "Thirty"),
    guide = ggplot2::guide_colourbar(position = "bottom")) +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"),
                 axis.title.x = ggplot2::element_text(colour = "grey42"))
""",
        encoding="utf-8",
    )
    spec = execspec.desktop_native_spec(
        str(script), interpreter=rscript, cwd=str(tmp_path), project_root=str(tmp_path)
    )
    registry = nativesession.NativeSessionRegistry()
    target = nativeggplot.GgplotTarget("plot", (432, 288))
    stem = target.stem(spec.target)
    session = desktopproc.start(
        desktopproc.OwnedLaunch(spec, ggplot_target=target),
        registry=registry,
    )
    try:
        state = session.wait_for_state(
            {nativesession.BARRIER, *nativesession.TERMINAL_STATES}, timeout=20
        )
        assert state == nativesession.BARRIER, session.script_error or session.public_state()
        assert registry.list(str(tmp_path)) == [session]
        assert session.session_id in envlease.native_sessions_on(spec.interpreter)
        baseline = session.ensure_built()
        assert baseline["source"] == "ggplot2"
        assert list(baseline["stems"]) == [stem]
        (descriptor,) = [
            figcapture.descriptor_from_payload(desc) for desc in baseline["descriptors"]
        ]
        assert descriptor.capture_source == "ggplot2"
        assert descriptor.original_artifact is None
        assert not descriptor.can_writeback_artifact
        assert not registry.bind_assets(session)
        assert registry.route_for(str(tmp_path), descriptor.script, stem) is session
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        manifest = baseline["manifest"]
        assert manifest["size_mm"] == pytest.approx([152.4, 101.6])
        elements = {element["gid"]: element for element in manifest["elements"]}
        scatter = elements["layer.1.scatter"]
        assert scatter["role"] == "scatter" and scatter["identity"].startswith("r6:")
        assert {field["prop"] for field in scatter["editable"]} == {"size", "marker", "alpha"}
        point_patch = {"gid": scatter["gid"], "prop": "marker", "value": "s"}
        for invalid_point in (point_patch, {**point_patch, "identity": "r6:old-series"}):
            with pytest.raises(pool.WorkerError, match="point series changed"):
                session.override(stem, [invalid_point], inline_svg=True)
        changed = session.override(
            stem, [{**point_patch, "identity": scatter["identity"]}], inline_svg=True
        )
        changed_scatter = next(
            e for e in changed["manifest"]["elements"] if e["gid"] == scatter["gid"]
        )
        marker = next(field for field in changed_scatter["editable"] if field["prop"] == "marker")
        assert marker["value"] == "s" and marker["value_original"] == "original"
        assert "<polygon " in changed["svg"] and changed["svg"] != baseline["svg"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        title = elements["plot.title"]
        assert title["label"] == "Fuel economy"
        assert title["identity"] == "r1:568c4e10e2a02cd8"
        x, y, width, height = title["bbox"]
        assert 0 <= x < x + width <= 1 and 0 <= y < y + height <= 1
        assert {item["prop"]: item["value"] for item in title["editable"]} == {
            "color": "#112233",
            "weight": "normal",
            "style": "normal",
            "fontfamily": "sans-serif",
            "fontsize": 13.2,
        }
        top = elements["axis.x.top.title"]
        bottom = elements[X_BOTTOM]
        assert top["label"] == bottom["label"] == "Engine displacement"
        assert top["identity"] != bottom["identity"]
        assert bottom["editable"][0]["value"] == "#6B6B6B"
        typography_patches = [
            {"gid": X_BOTTOM, "prop": prop, "value": value, "identity": bottom["identity"]}
            for prop, value in (("weight", "bold"), ("style", "italic"), ("fontsize", 19.25))
        ]
        plain_png = session.preview_png_snapshot(stem, [], 400)
        edited_png = session.preview_png_snapshot(stem, typography_patches, 400)
        with (
            Image.open(io.BytesIO(base64.b64decode(plain_png["png"]))) as plain,
            Image.open(io.BytesIO(base64.b64decode(edited_png["png"]))) as edited,
        ):
            assert plain.size == edited.size == (400, 267)
            assert plain.tobytes() != edited.tobytes()
        assert session.preview_png(stem, typography_patches, 400, "font-preview").read_bytes() == (
            base64.b64decode(edited_png["png"])
        )
        axis = next(
            element for element in edited_png["manifest"]["elements"] if element["gid"] == X_BOTTOM
        )
        assert axis["identity"] == bottom["identity"]
        assert {item["prop"]: item["value"] for item in axis["editable"]} == {
            "color": "#6B6B6B",
            "weight": "bold",
            "style": "italic",
            "fontfamily": "sans-serif",
            "fontsize": 19.25,
        }
        size_field = next(field for field in axis["editable"] if field["prop"] == "fontsize")
        assert size_field["value_original"] == 11
        original_ticks = next(
            el for el in plain_png["manifest"]["elements"] if el["gid"] == TICKS_BOTTOM
        )
        resized_ticks = next(
            el for el in edited_png["manifest"]["elements"] if el["gid"] == TICKS_BOTTOM
        )
        assert resized_ticks["bbox"][1] < original_ticks["bbox"][1]
        assert axis["bbox"][1] > resized_ticks["bbox"][1] + resized_ticks["bbox"][3]
        for invalid in (0, -2.5, "19.25"):
            with pytest.raises(pool.WorkerError, match="font size must be a positive number"):
                session.override(
                    stem, [{**typography_patches[-1], "value": invalid}], inline_svg=True
                )
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        tick_group = elements[TICKS_BOTTOM]
        assert tick_group["label"] == "下轴刻度文字"
        assert tick_group["role"] == "ticks" and not tick_group["draggable"]
        assert tick_group["identity"].startswith("r2:")
        assert "tick_labels" not in tick_group
        assert tick_group["identity"] != elements[TICKS_TOP]["identity"]
        tick_patch = {"gid": TICKS_BOTTOM, "prop": "color", "value": "#A64184"}
        for invalid_tick in (
            tick_patch,
            {**tick_patch, "identity": elements[TICKS_TOP]["identity"]},
        ):
            with pytest.raises(pool.WorkerError, match="text target changed"):
                session.override(stem, [invalid_tick], inline_svg=True)
        tick_patch["identity"] = tick_group["identity"]
        tick_render = session.override(stem, [tick_patch], inline_svg=True)
        assert "#A64184" in tick_render["svg"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        bar_ticks = elements[COLOURBAR_TICKS]
        assert bar_ticks["label"] == "色条刻度 “City mileage”"
        assert bar_ticks["role"] == "legend_text" and not bar_ticks["draggable"]
        assert bar_ticks["identity"].startswith("r2:")
        assert "tick_labels" not in bar_ticks and "legend_title" not in bar_ticks
        bar_patch = {"gid": COLOURBAR_TICKS, "prop": "color", "value": "#C32146"}
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(stem, [bar_patch], inline_svg=True)
        bar_patch["identity"] = bar_ticks["identity"]
        bar_preview = session.override(stem, [bar_patch], inline_svg=True)["svg"]
        assert bar_preview.count("#C32146") == 3
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        legend = elements["legend.bottom"]
        move = {"gid": legend["gid"], "prop": "loc_frac", "identity": legend["identity"]}
        for invalid in ([], [0.4], [0.4, "0.6"], {"x": 0.4, "y": 0.6}):
            with pytest.raises(pool.WorkerError, match="two finite numbers"):
                session.override(stem, [{**move, "value": invalid}], inline_svg=True)
        moved_png = session.preview_png_snapshot(stem, [{**move, "value": [0.32, 0.8]}], 400)
        moved_legend = next(
            el for el in moved_png["manifest"]["elements"] if el["gid"] == legend["gid"]
        )
        assert moved_legend["anchor"] == pytest.approx([0.32, 0.8], abs=1e-7)
        assert moved_legend["identity"] == legend["identity"]
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
        for prop, value in (("text", "Unchanged"), ("weight", "semibold")):
            with pytest.raises(pool.WorkerError, match="text style is unavailable"):
                session.override(
                    stem, [{"gid": "plot.title", "prop": prop, "value": value}], inline_svg=True
                )
        with pytest.raises(pool.WorkerError, match="text is unavailable"):
            session.override(
                stem,
                [{"gid": "axis.y.right.title", "prop": "color", "value": "#6C3799"}],
                inline_svg=True,
            )
        color = [
            {
                "gid": "plot.title",
                "prop": "color",
                "value": "#6C3799",
                "identity": title["identity"],
            }
        ]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(stem, [{**color[0], "identity": "r1:another-title"}], inline_svg=True)
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                stem,
                [{**color[0], "gid": X_BOTTOM, "identity": top["identity"]}],
                inline_svg=True,
            )
        assert session.svg_path(stem).read_text(encoding="utf-8") == baseline["svg"]
        changed = session.override(stem, color, inline_svg=True)
        edited = changed["svg"]
        changed_title = next(
            element for element in changed["manifest"]["elements"] if element["gid"] == "plot.title"
        )
        assert changed_title["bbox"] == title["bbox"]
        assert changed_title["editable"][0]["value"] == "#6C3799"
        assert edited != baseline["svg"]
        assert edited.replace("#6C3799", "#112233", 1) == baseline["svg"]
        pdf = tmp_path / "rendered.pdf"
        companion = tmp_path / "rendered.svg"
        exported = session.export(stem, color, str(pdf), svg_path=str(companion))
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert companion.read_text(encoding="utf-8") == edited
        assert exported["svg"]["size_bytes"] == len(edited.encode("utf-8"))
        assert session.override(stem, [], inline_svg=True)["svg"] == baseline["svg"]
    finally:
        assert session.cancel_owned().confirmed
    assert session.session_id not in envlease.native_sessions_on(spec.interpreter)


def test_desktop_ggplot_edit_uses_the_layout_archive_and_mixed_export(tmp_path, monkeypatch):
    from tavotto import app as service, security
    from tavotto.engine import documentassets
    from test_chart_endpoints import BIND_REQUEST, CORPUS

    rscript, explicit = _rscript()
    if explicit is None:
        pytest.skip("set TAVOTTO_TEST_RSCRIPT to verify the full desktop route")
    home = tmp_path / "warm"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(home))
    root = tmp_path / "混合 项目"
    root.mkdir()
    script = root / "Fuel economy.R"
    script.write_text(
        """
cat("computed", file = "r-witness")
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy, colour = drv)) +
  ggplot2::geom_point() +
  ggplot2::scale_colour_discrete(labels = c("Four wheel drive", "Front wheel drive",
                                           "Rear wheel drive")) +
  ggplot2::facet_wrap(ggplot2::vars(cyl), labeller = ggplot2::labeller(
    cyl = function(values) paste("Cylinder", values))) +
  ggplot2::labs(title = "Fuel economy", subtitle = "Comparing drive configurations",
                 caption = "Source: ggplot2 mpg", x = "Engine displacement", y = "Highway mileage",
                 colour = "Drive type") +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"),
                 legend.text = ggplot2::element_text(colour = "#294052"))
""",
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; "
            "plt.plot([1, 2, 3], [4, 6, 5]); plt.savefig('reference.pdf')",
        ],
        cwd=root,
        check=True,
        timeout=_COLD_RENDER_TIMEOUT_SECONDS,
    )
    nonce = "native-ggplot-test"
    auth = security.SessionState(nonce)
    monkeypatch.setitem(service.app.config, "TESTING", True)
    monkeypatch.setitem(service.app.config, "TAVOTTO_DESKTOP_MODE", True)
    monkeypatch.setitem(service.app.config, security.STATE_KEY, auth)
    original_safe_worker = service._safe_worker

    def safe_worker(script, *args, **kwargs):
        assert not script.endswith(".R"), "An R asset was dispatched to a Python worker"
        return original_safe_worker(script, *args, **kwargs)

    monkeypatch.setattr(service, "_safe_worker", safe_worker)
    client = service.app.test_client()
    service.open_project(str(root))
    session = None
    try:
        target = {
            "script": script.name,
            "object": "plot",
            "frame_pt": [432, 288],
            "rscript": rscript,
        }
        unauthorized = client.post("/api/native/ggplot2", json=target)
        assert unauthorized.status_code != 202
        assert not (root / "r-witness").exists()
        client.set_cookie(security.COOKIE_NAME, auth.redeem(nonce))
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state((nativesession.BARRIER,), timeout=20) == nativesession.BARRIER
        build_url = f"/api/native/sessions/{sid}/build"
        built = client.post(build_url)
        assert built.status_code == 200, built.get_json()
        (descriptor,) = built.get_json()["descriptors"]
        source = descriptor["asset_id"]
        assert (root / "r-witness").read_text() == "computed"

        def render(patches):
            result = client.post(
                "/api/engine/render", json={"id": source, "patches": patches, "inline_svg": True}
            )
            assert result.status_code == 200, result.get_json()
            return result.get_json()

        elements = {element["gid"]: element for element in render([])["manifest"]["elements"]}
        assert elements[LEGEND]["role"] == "legend_text"
        assert elements[LEGEND]["label"] == "Drive type"
        assert elements[LEGEND_ENTRY]["role"] == "legend_text"
        assert elements[LEGEND_ENTRY]["label"] == "Front wheel drive"
        assert elements[FACET]["role"] == "text"
        assert elements[FACET]["label"] == "Cylinder 8"
        assert elements[SUBTITLE]["role"] == elements[CAPTION]["role"] == "text"
        assert elements[SUBTITLE]["label"] == "Comparing drive configurations"
        assert elements[CAPTION]["label"] == "Source: ggplot2 mpg"
        expected = {
            "plot.title": "#6C3799",
            SUBTITLE: "#B04964",
            CAPTION: "#2B7984",
            X_BOTTOM: "#8C3011",
            Y_LEFT: "#2A7654",
            LEGEND: "#2370B1",
            LEGEND_ENTRY: "#E058A9",
            FACET: "#BE3D47",
        }
        patches = [
            {"gid": gid, "prop": "color", "value": color, "identity": elements[gid]["identity"]}
            for gid, color in expected.items()
        ]

        edit = render(patches)
        svg = edit["svg"]
        for element in edit["manifest"]["elements"]:
            gid = element["gid"]
            if gid == LEGEND_BOX:
                assert element["editable"] == []
                continue
            assert element["editable"][0]["value"] == expected.get(
                gid, elements[gid]["editable"][0]["value"]
            )
        other_facet = elements["facet.t.1.1.label"]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                descriptor["stem"],
                [*patches[:-1], {**patches[-1], "identity": other_facet["identity"]}],
                inline_svg=True,
            )
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        entry_patch = next(patch for patch in patches if patch["gid"] == LEGEND_ENTRY)
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(
                descriptor["stem"],
                [{key: value for key, value in entry_patch.items() if key != "identity"}],
                inline_svg=True,
            )
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        without_entry = render([patch for patch in patches if patch["gid"] != LEGEND_ENTRY])["svg"]
        assert "#E058A9" not in without_entry and "#2370B1" in without_entry
        without_caption = render([patch for patch in patches if patch["gid"] != CAPTION])["svg"]
        assert "#2B7984" not in without_caption and "#B04964" in without_caption
        without_facet = render(patches[:-1])["svg"]
        assert "#2370B1" in without_facet and "#BE3D47" not in without_facet
        undo_x_and_y = render(patches[:1])["svg"]
        assert (
            "#6C3799" in undo_x_and_y
            and "#8C3011" not in undo_x_and_y
            and "#2A7654" not in undo_x_and_y
        )
        assert render(patches)["svg"] == svg
        # The second window's build cannot relabel the edited SVG as baseline.
        assert client.post(build_url).status_code == 200
        assert session.svg_path(descriptor["stem"]).read_text(encoding="utf-8") == svg
        cache = client.post("/api/runtime/preview", json={"id": source, "patches": patches})
        assert cache.headers["X-Tavotto-Preview-Exact"] == "1"
        assert cache.get_data(as_text=True) == svg

        legend = elements[LEGEND_BOX]
        assert (legend["role"], legend["draggable"], legend["drag_prop"]) == (
            "legend",
            True,
            "loc_frac",
        )
        move = {
            "gid": LEGEND_BOX,
            "prop": "loc_frac",
            "value": [0.24, 0.6],
            "identity": legend["identity"],
        }
        prior_svg = svg
        moved = render([*patches, move])
        by_id = {element["gid"]: element for element in moved["manifest"]["elements"]}
        assert by_id[LEGEND_BOX]["anchor"] == pytest.approx(move["value"], abs=1e-7)
        assert by_id[LEGEND_BOX]["bbox"][2:] == pytest.approx(legend["bbox"][2:])
        assert by_id[LEGEND_ENTRY]["drag_host"] == LEGEND_BOX
        assert by_id[FACET]["bbox"] == elements[FACET]["bbox"]
        dx, dy = [end - start for end, start in zip(move["value"], legend["anchor"], strict=True)]
        for gid in (LEGEND, LEGEND_ENTRY):
            x, y, w, h = elements[gid]["bbox"]
            assert by_id[gid]["bbox"] == pytest.approx([x + dx, y + dy, w, h], abs=1e-7)

        svg = moved["svg"]
        ns = "{http://www.w3.org/2000/svg}"
        old_group = ElementTree.fromstring(prior_svg).find(f".//{ns}g[@id='{LEGEND_BOX}']")
        group = ElementTree.fromstring(svg).find(f".//{ns}g[@id='{LEGEND_BOX}']")
        assert old_group is not None and group is not None
        old_labels = {text.text: text for text in old_group.iter(ns + "text")}
        assert set(old_labels) >= {"Drive type", "Front wheel drive", "Rear wheel drive"}
        assert {text.text for text in group.iter(ns + "text")} == set(old_labels)
        for text in group.iter(ns + "text"):
            previous = old_labels[text.text]
            assert float(text.attrib["x"]) - float(previous.attrib["x"]) == pytest.approx(
                dx * 432, abs=0.02
            )
            assert float(text.attrib["y"]) - float(previous.attrib["y"]) == pytest.approx(
                dy * 288, abs=0.02
            )
        assert render(patches)["svg"] == prior_svg
        patches.append(move)
        assert render(patches)["svg"] == svg

        panels = [
            {
                "id": "mpl",
                "type": "panel",
                "fileId": "reference.pdf",
                "fileKind": "pdf",
                "x": 4,
                "y": 8,
                "w": 68,
                "h": 50,
                "nativeW": 68,
                "nativeH": 50,
                "figureFrame": 1,
                "overrides": [],
            },
            {
                "id": "r",
                "type": "panel",
                "fileId": source,
                "fileKind": "runtime",
                "x": 80,
                "y": 8,
                "w": 75,
                "h": 50,
                "nativeW": 75,
                "nativeH": 50,
                "figureFrame": 1,
                "overrides": patches,
                "source": {
                    "captureSource": "ggplot2",
                    "script": descriptor["script"],
                    "entry": descriptor["entry"],
                    "stem": descriptor["stem"],
                },
            },
        ]
        layout = "/api/layouts/Mixed%20native"
        saved = client.post(
            layout + "?target=project",
            json={
                "schema": 3,
                "project": {"id": "mixed", "name": "Mixed native"},
                "canvases": [
                    {
                        "id": "page",
                        "name": "Matplotlib and ggplot2",
                        "schema": 2,
                        "page": {"w": 160, "h": 65},
                        "objects": panels,
                        "guides": [],
                    },
                ],
                "activeCanvasId": "page",
                "createdAt": 1,
                "updatedAt": 1,
            },
        )
        assert saved.status_code == 200, saved.get_json()
        assert "r" in saved.get_json()["figure_snapshots"]
        saved_document = client.get(layout).get_json()
        reference = saved_document["canvases"][0]["objects"][1]["figureSnapshot"]
        assert session.cancel_owned().confirmed
        session = None
        # Reopening can autosave before the sidecar itself restarts.
        slot = "/api/autosave/d_native_ggplot_stopped"
        autosaved = client.put(slot, json=saved_document)
        assert autosaved.status_code == 200, autosaved.get_json()
        assert (
            client.get(slot).get_json()["canvases"][0]["objects"][1]["figureSnapshot"] == reference
        )
        original_request = {
            "scope": "original",
            "filename": "Archived original",
            "formats": ["pdf"],
            "original": {
                "figure_id": source,
                "overrides": patches,
                "figure_snapshot": reference,
            },
        }
        exported = client.post("/api/export", json=original_request)
        assert exported.status_code == 200, exported.get_json()
        original = exported.get_json()
        assert original["status"] == "done", original
        colors = _pdf_text_colors(Path(original["export_dir"]) / original["outputs"][0]["name"])
        assert colors["Cylinder 8"] == {int(expected[FACET][1:], 16)}
        assert colors["Front wheel drive"] == {int(expected[LEGEND_ENTRY][1:], 16)}
        assert colors["Rear wheel drive"] == {0x294052}
        assert colors["Comparing drive configurations"] == {int(expected[SUBTITLE][1:], 16)}
        assert colors["Source: ggplot2 mpg"] == {int(expected[CAPTION][1:], 16)}

        original_script = script.read_text(encoding="utf-8")
        script.write_text('stop("new execution failed")\n', encoding="utf-8")
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state(nativesession.TERMINAL_STATES, timeout=20)
        assert session.execution_id
        stale = client.post("/api/export", json={**original_request, "filename": "Stale original"})
        assert stale.get_json().get("error", {}).get("code") == "svg_snapshot_missing"
        assert not (root / "tavottofile" / "export" / "Stale original.pdf").exists()
        # The same GID and label in a renamed guide must reject the old edit.
        script.write_text(
            original_script.replace('"Drive type"', '"Other drive grouping"'), encoding="utf-8"
        )
        launched = client.post("/api/native/ggplot2", json=target)
        assert launched.status_code == 202, launched.get_json()
        sid = launched.get_json()["session"]["session_id"]
        session = nativesession.REGISTRY.get(sid)
        assert session.wait_for_state((nativesession.BARRIER,), timeout=20) == nativesession.BARRIER
        assert client.post(f"/api/native/sessions/{sid}/build").status_code == 200
        regrouped = {element["gid"]: element for element in render([])["manifest"]["elements"]}
        assert regrouped[LEGEND_ENTRY]["label"] == elements[LEGEND_ENTRY]["label"]
        assert regrouped[LEGEND_ENTRY]["identity"] != entry_patch["identity"]
        with pytest.raises(pool.WorkerError, match="text target changed"):
            session.override(descriptor["stem"], [entry_patch], inline_svg=True)
        with pytest.raises(pool.WorkerError, match="legend changed"):
            session.override(descriptor["stem"], [move], inline_svg=True)
        assert session.cancel_owned().confirmed
        session = None
        service.reset_projects()
        script.rename(root / "source-moved")
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "cold"))
        service.open_project(str(root))

        opened = client.get(layout)
        assert opened.status_code == 200, opened.get_json()
        mpl, r = opened.get_json()["canvases"][0]["objects"]
        assert r["overrides"] == patches
        reference = r["figureSnapshot"]
        archive = {"id": source, "patches": patches, "figure_snapshot": reference}
        for endpoint in ("/api/figure-preview", "/api/documents/v4/figure-preview"):
            preview = client.post(endpoint, json=archive)
            assert preview.status_code == 200, preview.get_json()
            assert preview.get_json()["svg"] == svg
            mismatch = client.post(endpoint, json={**archive, "patches": []})
            assert mismatch.status_code == 422, mismatch.get_json()

        exported = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": "Mixed native",
                "formats": ["pdf"],
                "canvas": {
                    "page_w_mm": 160,
                    "page_h_mm": 65,
                    "objects": [
                        {
                            "type": "panel",
                            "id": mpl["fileId"],
                            "x_mm": 4,
                            "y_mm": 8,
                            "w_mm": 68,
                            "h_mm": 50,
                        },
                        {
                            "type": "panel",
                            "id": source,
                            "x_mm": 80,
                            "y_mm": 8,
                            "w_mm": 75,
                            "h_mm": 50,
                            "overrides": patches,
                            "figure_snapshot": reference,
                        },
                    ],
                },
            },
        )
        assert exported.status_code == 200, exported.get_json()
        result = exported.get_json()
        assert result["status"] == "done" and result["outputs"][0]["format"] == "pdf"
        colors = _pdf_text_colors(Path(result["export_dir"]) / result["outputs"][0]["name"])
        for gid, color in expected.items():
            assert colors[elements[gid]["label"]] == {int(color[1:], 16)}
        assert all(not active.alive() for active in nativesession.REGISTRY.list(str(root)))

        (root / "chart.svg").write_bytes((CORPUS / "line_two_series.svg").read_bytes())
        inspected = client.get("/api/svg/chart/inspect?id=chart.svg")
        assert inspected.status_code == 200, inspected.get_json()
        binding = client.post(
            "/api/svg/chart/bind",
            json={
                **BIND_REQUEST,
                "file": "chart.svg",
                "scene_digest": inspected.json["scene_digest"],
            },
        )
        assert binding.status_code == 200, binding.get_json()
        chart = {key: binding.get_json()[key] for key in ("model", "binding")}
        mixed_document = opened.get_json()
        mixed_document["schema"] = 4
        mixed_document["canvases"].append(
            {
                "id": "bound-chart",
                "schema": 2,
                "name": "Computed SVG",
                "page": {"w": 140, "h": 80},
                "guides": [],
                "objects": [
                    {
                        "id": "chart-panel",
                        "type": "panel",
                        "fileId": "chart.svg",
                        "fileKind": "svg",
                        "chart": chart,
                        "x": 5,
                        "y": 5,
                        "w": 110,
                        "h": 60,
                        "nativeW": 110,
                        "nativeH": 60,
                        "figureFrame": 1,
                        "overrides": [],
                    }
                ],
            }
        )
        named = "/api/layouts/Portable%20mixed"
        saved = client.post(named + "?target=project", json=mixed_document)
        assert saved.status_code == 200, saved.get_json()
        mixed_document = client.get(named).get_json()
        chart_reference = mixed_document["canvases"][1]["objects"][0]["chartSource"]
        original_chart = (root / "chart.svg").read_bytes()
        (root / "chart.svg").unlink()
        packed = client.post("/api/package", json={"doc": mixed_document})
        assert packed.status_code == 200, packed.get_json()
        package = client.get(packed.get_json()["url"]).get_data()

        service.reset_projects()
        monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "receiver data"))
        receiver = tmp_path / "收件人 项目"

        def upload(action, **fields):
            return client.post(
                f"/api/package/{action}",
                data={"package": (io.BytesIO(package), "portable.tavotto"), **fields},
            )

        assert upload("open").get_json() == {"document_format": "v4"}
        inspection = upload("inspect")
        assert inspection.status_code == 200, inspection.get_json()
        assert inspection.get_json()["missing"] == [] and not receiver.exists()
        result = upload("install", options=json.dumps({"destination": str(receiver)}))
        assert result.status_code == 201, result.get_json()
        assert not (receiver / script.name).exists() and not (receiver / "chart.svg").exists()
        assert (
            documentassets.source_path(receiver / "tavottofile", chart_reference).read_bytes()
            == original_chart
        )
        service.open_project(str(receiver))
        assert client.post("/api/figure-preview", json=archive).get_json()["svg"] == svg
        persisted = client.post(named + "?target=project", json=result.get_json()["doc"])
        assert persisted.status_code == 200, persisted.get_json()
        assert not nativesession.REGISTRY.list(str(receiver))
    finally:
        if session is not None:
            session.cancel_owned()
        service.reset_projects()
