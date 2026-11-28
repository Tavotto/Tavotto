# Both an owned Rscript launch and a caller's current R console share the
# same captured figure and authenticated command loop.
ggplot_adapter <- function(path) {
  if (!requireNamespace("ggplot2", quietly = TRUE) ||
      !requireNamespace("svglite", quietly = TRUE)) {
    stop("The selected R needs ggplot2 and svglite.")
  }
  adapter <- new.env(parent = baseenv())
  sys.source(path, envir = adapter)
  native <- dirname(path)
  for (resource in c("ggplot_objects.R", "ggplot_pigment.R", "ggplot_aesthetic_observation.R", "ggplot_colour_mapping.R",
                     "ggplot_colourbar_layout.R", "ggplot_colour_scale.R",
                     "ggplot_guide_order.R", "ggplot_point_style.R", "ggplot_points.R",
                     "ggplot_bar_geometry.R", "ggplot_interval_geometry.R", "ggplot_curve_style.R", "ggplot_bars.R",
                     "ggplot_ribbon_geometry.R", "ggplot_ribbons.R", "ggplot_raster_geometry.R", "ggplot_transforms.R",
                     "ggplot_axis_projection.R", "ggplot_axes.R", "ggplot_frame_geometry.R", "ggplot_frame.R", "ggplot_panel_theme_geometry.R",
                     "ggplot_title_text.R", "ggplot_tick_source.R", "ggplot_tick_labels.R", "ggplot_tick_locators.R")) {
    sys.source(file.path(native, resource), envir = adapter)
  }
  adapter
}

ggplot_source_plot <- function(path, object_name, before_run = function() NULL) {
  user <- new.env(parent = globalenv())
  program <- parse(file = path, keep.source = TRUE, encoding = "UTF-8")
  before_run()
  eval(program, envir = user)
  if (!exists(object_name, envir = user, inherits = FALSE)) stop("The named ggplot object is absent.")
  get(object_name, envir = user, inherits = FALSE)
}

ggplot_serve <- function(spec, token, plot_for) {
  if (!is.character(token) || length(token) != 1L || !grepl("^[A-Za-z0-9_-]+$", token) ||
      !identical(spec$host, "127.0.0.1")) stop("Invalid native R connection.")
  control <- socketConnection(spec$host, as.integer(spec$port),
                              blocking = TRUE, open = "r+", timeout = 3600)
  on.exit(close(control))
  send_raw <- function(text) {
    writeLines(text, control, useBytes = TRUE)
    flush(control)
  }
  if (spec$console) {
    rscript <- file.path(R.home("bin"), if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript")
    hello <- list(bridge_hello = 1L, protocol_version = 1L, token = token, pid = Sys.getpid(),
                  interpreter = normalizePath(rscript, winslash = "/", mustWork = TRUE))
    replay <- c(hello, list(cwd = getwd(), libraries = as.list(.libPaths())))
    message <- jsonlite::toJSON(replay, auto_unbox = TRUE)
    if (nchar(message, type = "bytes") >= (spec$hello_limit %||% 0L)) {
      message <- jsonlite::toJSON(hello, auto_unbox = TRUE)
    }
    send_raw(message)
    rm(hello, replay, message)
  } else {
    send_raw(sprintf('{"bridge_hello":1,"protocol_version":1,"token":"%s","pid":%d}', token, Sys.getpid()))
  }
  rm(token)
  fail_before_json <- function(message) {
    # Only fixed messages call this path, before jsonlite is available.
    ack <- readLines(control, n = 1L, warn = FALSE)
    if (length(ack)) {
      send_raw(sprintf(paste0('{"bridge_event":"exit","code":1,"figures":0,',
                              '"script_error":{"message":"%s"}}'), message))
    }
    1L
  }
  # Apply the user-selected package library to this child before loading any
  # namespace, including jsonlite. Keep the existing interpreter defaults as
  # fallback and leave the caller's library and startup files untouched.
  if (!is.null(spec$library)) {
    lib <- tryCatch(normalizePath(spec$library, winslash = "/", mustWork = TRUE),
                    error = function(error) NULL)
    if (!is.null(lib)) .libPaths(c(lib, .libPaths()))
    if (!identical(.libPaths()[[1L]], lib)) {
      return(fail_before_json("The selected R package library is unavailable."))
    }
  }
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    return(fail_before_json("The selected R needs the jsonlite package."))
  }
  send <- function(frame) {
    send_raw(jsonlite::toJSON(frame, auto_unbox = TRUE, null = "null", digits = NA))
  }
  receive <- function() {
    line <- readLines(control, n = 1L, warn = FALSE, encoding = "UTF-8")
    if (length(line) == 0L) return(NULL)
    jsonlite::fromJSON(line, simplifyVector = FALSE)
  }
  ack <- receive()
  if (!isTRUE(ack$ok) || !identical(ack$bridge_hello, 1L)) {
    stop("Tavotto could not authenticate this R session.")
  }

  captured <- tryCatch({
    adapter <- ggplot_adapter(spec$adapter)
    native <- dirname(spec$adapter)
    palettes <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
    probe <- file.path(spec$out_dir, "cairo-device-check.pdf")
    previous <- grDevices::dev.cur()
    device <- adapter$open_ggplot_pdf(spec$frame_pt, probe)
    adapter$close_ggplot_device(device, previous)
    unlink(probe)
    start_execution <- function() {
      send(list(bridge_event = "execution_started", execution_id = spec$execution,
                script = spec$relative_script))
    }
    adapter$capture_ggplot(plot_for(start_execution), spec$frame_pt, palettes)
  }, error = function(error) {
    send(list(bridge_event = "exit", code = 1L, figures = 0L,
              script_error = list(message = substr(conditionMessage(error), 1L, 2000L))))
    NULL
  })
  if (is.null(captured)) return(1L)
  send(list(bridge_event = "barrier", reason = "script_end", stems = list(spec$stem)))

  edits_from <- function(request) {
    if (!identical(request$stem, spec$stem)) stop("Unknown ggplot object.")
    patches <- request$payload$patches
    if (!is.list(patches)) stop("Expected a full list of plot patches.")
    edits <- list()
    for (patch in patches) {
      if (!is.list(patch) ||
          !identical(sort(setdiff(names(patch), "identity")), c("gid", "prop", "value")) ||
          !is.character(patch$gid) || length(patch$gid) != 1L ||
          is.na(patch$gid) || !nzchar(patch$gid) ||
          !is.character(patch$prop) || length(patch$prop) != 1L ||
          is.na(patch$prop) || !nzchar(patch$prop) ||
          (!is.character(patch$value) && !is.numeric(patch$value) &&
           !(patch$prop %in% c("loc_frac", "xlim", "ylim", "major_values") && is.list(patch$value)) &&
           !(patch$prop %in% c("invert_x", "invert_y", "grid_x", "grid_y", "minor_visible", "visible", "outline_visible",
                               adapter$ggplot_frame_visibility, adapter$ggplot_tick_side_keys) &&
             is.logical(patch$value)))) {
        stop("Invalid native R edit.")
      }
      edits[[patch$gid]][[patch$prop]] <- patch$value
    }
    edits
  }

  repeat {
    request <- receive()
    if (is.null(request)) return(0L)
    if (!identical(request$protocol_version, 1L) || !is.character(request$request_id)) {
      stop("Invalid native worker envelope.")
    }
    if (request$cmd %in% c("terminate", "shutdown")) return(0L)
    envelope <- request[c("protocol_version", "request_id", "worker_generation",
                          "render_revision")]
    if (request$cmd %in% c("preview_png_existing", "render_existing")) {
      if (!identical(request$payload$expected_execution_id, spec$execution)) {
        answer <- list(ok = FALSE, error = list(code = "svg_source_unavailable",
                       message = "The R capture belongs to a different execution."))
        send(c(envelope, list(execution_id = spec$execution), answer))
        next
      }
      request$cmd <- sub("_existing$", "", request$cmd)
    }
    answer <- tryCatch(
      switch(request$cmd,
        build = list(ok = TRUE, stems = stats::setNames(list(list()), spec$stem),
                     svg = captured$svg, source = "ggplot2",
                     manifest = adapter$ggplot_manifest(captured$state, spec$stem),
                     ggplot2 = as.character(utils::packageVersion("ggplot2"))),
        render = {
          edits <- edits_from(request)
          result <- list(ok = TRUE, svg = adapter$render_ggplot(captured$state, edits),
                         manifest = adapter$ggplot_manifest(captured$state, spec$stem, edits))
          if (isTRUE(request$payload$include_fonts)) {
            result$fonts <- as.list(unique(systemfonts::system_fonts()$path))
          }
          result
        },
        preview_png = {
          edits <- edits_from(request)
          payload <- request$payload
          paired <- isTRUE(payload$with_manifest)
          if (paired) {
            path <- tempfile(pattern = "preview-", tmpdir = spec$out_dir, fileext = ".png")
          } else {
            tag <- payload$tag
            if (!is.character(tag) || length(tag) != 1L || is.na(tag) ||
                !grepl("^[A-Za-z0-9_-]+$", tag)) stop("Invalid native R preview tag.")
            path <- file.path(spec$out_dir, paste0(spec$stem, "__", tag, ".png"))
          }
          tryCatch({
            measured <- adapter$preview_ggplot_png(captured$state, edits, path, payload$width, paired)
            if (paired) {
              list(ok = TRUE, png = jsonlite::base64_enc(readBin(path, "raw", file.info(path)$size)),
                   manifest = adapter$ggplot_manifest(captured$state, spec$stem, edits, measured))
            } else {
              list(ok = TRUE, path = path)
            }
          }, finally = if (paired) unlink(path))
        },
        export = {
          edits <- edits_from(request)
          payload <- request$payload
          if (!identical(payload$format, "pdf") || !is.character(payload$path)) {
            stop("The ggplot adapter exports a PDF with an optional SVG companion.")
          }
          adapter$export_ggplot_pdf(captured$state, edits, payload$path)
          companion <- NULL
          if (!is.null(payload$svg_path)) {
            companion <- adapter$render_ggplot(captured$state, edits)
          }
          list(ok = TRUE, path = payload$path, svg_companion = companion)
        },
        capture_data_fingerprint = {
          if (!isTRUE(spec$console) || !identical(request$stem, spec$stem)) {
            stop("The current R object cannot supply capture data.")
          }
          list(ok = TRUE, data_fingerprint = adapter$ggplot_data_fingerprint(captured$state))
        },
        continue = list(ok = TRUE, released = TRUE),
        list(ok = FALSE, error = list(code = "unknown_cmd", message = "Unsupported R operation."))
      ),
      error = function(error) {
        list(ok = FALSE, error = list(code = "bad_request", message = conditionMessage(error)))
      }
    )
    send(c(envelope, list(execution_id = spec$execution), answer))
    if (identical(request$cmd, "continue")) break
  }
  send(list(bridge_event = "exit", code = 0L, figures = 1L))
  0L
}

tavotto_attach <- function(plot, offer_path) {
  # The console stays in the current process, with its libraries,
  # working directory and graphics devices. All plotting is scoped.
  if (!file.exists(offer_path)) stop("This Tavotto connection command was used or has expired.")
  offer <- jsonlite::fromJSON(offer_path)
  if (!isTRUE(offer$spec$console)) stop("Invalid Tavotto console invitation.")
  offer$spec$library <- NULL
  status <- ggplot_serve(offer$spec, offer$token, function(start_execution) plot)
  if (status != 0L) stop("Tavotto could not capture the current ggplot object.")
  invisible(TRUE)
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  token <- Sys.getenv("TAVOTTO_BRIDGE_TOKEN")
  Sys.unsetenv("TAVOTTO_BRIDGE_TOKEN")
  if (!length(args) %in% 11:12) stop("Invalid owned R launch.")
  spec <- list(console = FALSE, frame_pt = as.numeric(args[3:4]), host = args[[5L]],
               port = args[[6L]], adapter = args[[7L]], stem = args[[8L]],
               execution = args[[9L]], relative_script = args[[10L]], out_dir = args[[11L]],
               library = if (length(args) == 12L) args[[12L]])
  status <- ggplot_serve(spec, token, function(start_execution) {
    ggplot_source_plot(args[[1L]], args[[2L]], start_execution)
  })
  if (status != 0L) quit(status = status)
}

if (sys.nframe() == 0L) main()
