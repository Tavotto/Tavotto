# This child receives its paths from Tavotto. It runs in a disposable project
# with filesystem protection around the original project and its inputs.
args <- commandArgs(trailingOnly = TRUE)
request <- jsonlite::fromJSON(args[[1L]], simplifyVector = FALSE)
bridge <- new.env(parent = baseenv())
sys.source(request$bridge, envir = bridge)
adapter <- bridge$ggplot_adapter(request$adapter)

if (identical(request$operation, "anchor")) {
  expressions <- parse(file = request$script, keep.source = TRUE, encoding = "UTF-8")
  binding <- as.name(request$object)
  definitions <- which(vapply(expressions, function(expression) {
    is.call(expression) && length(expression) == 3L &&
      (identical(expression[[1L]], as.name("<-")) || identical(expression[[1L]], as.name("="))) &&
      identical(expression[[2L]], binding)
  }, logical(1L)))
  if (!length(definitions) || any(diff(definitions) != 1L)) {
    stop("The selected plot needs a consecutive sequence of top-level assignments.")
  }
  # Place the edit after the last assignment without crossing unrelated
  # top-level expressions, such as a separate plot export.
  final <- definitions[[length(definitions)]]
  end <- as.integer(attr(expressions, "srcref")[[final]])
  jsonlite::write_json(
    list(line = end[[3L]], byte = end[[4L]], themes = as.list(adapter$ggplot_title_theme_names),
         tick_themes = as.list(adapter$ggplot_axis_tick_theme_names)),
    request$result, auto_unbox = TRUE
  )
} else if (identical(request$operation, "replay")) {
  if (length(request$fonts)) systemfonts::add_fonts(unlist(request$fonts, use.names = FALSE))
  frame <- unlist(request$frame_pt, use.names = FALSE)
  palettes <- jsonlite::fromJSON(request$palettes, simplifyVector = FALSE)
  plot <- bridge$ggplot_source_plot(request$script, request$object)
  captured <- adapter$capture_ggplot(plot, frame, palettes)
  edits <- list()
  for (patch in request$residual) edits[[patch$gid]][[patch$prop]] <- patch$value
  measured <- NULL
  if (!is.null(request$png)) {
    measured <- adapter$preview_ggplot_png(captured$state, edits, request$png, request$width, TRUE)
  }
  if (!is.null(request$pdf)) adapter$export_ggplot_pdf(captured$state, edits, request$pdf)
  if (!is.null(request$svg)) {
    writeLines(adapter$render_ggplot(captured$state, edits), request$svg, useBytes = TRUE)
  }
  manifest <- adapter$ggplot_manifest(captured$state, request$stem, edits, measured)
  if (isTRUE(request$include_data_fingerprint)) {
    manifest$data_fingerprint <- adapter$ggplot_data_fingerprint(captured$state)
  }
  jsonlite::write_json(manifest, request$result, auto_unbox = TRUE, digits = NA, null = "null")
} else {
  stop("Invalid ggplot source replay.")
}
