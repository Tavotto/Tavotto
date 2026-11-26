# Owned R process. User output stays on stdout and stderr. Control uses the
# existing local bridge envelope on a separate authenticated connection.

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  token <- Sys.getenv("TAVOTTO_BRIDGE_TOKEN")
  Sys.unsetenv("TAVOTTO_BRIDGE_TOKEN")
  if (!grepl("^[A-Za-z0-9_-]+$", token) || !length(args) %in% 11:12) {
    stop("Invalid owned R launch.")
  }

  script <- args[[1L]]
  object <- args[[2L]]
  stem <- args[[8L]]
  execution <- args[[9L]]
  frame_pt <- as.numeric(args[3:4])
  control <- socketConnection(args[[5L]], as.integer(args[[6L]]),
                              blocking = TRUE, open = "r+", timeout = 3600)
  on.exit(close(control))
  send_raw <- function(text) {
    writeLines(text, control, useBytes = TRUE)
    flush(control)
  }
  send_raw(sprintf('{"bridge_hello":1,"protocol_version":1,"token":"%s","pid":%d}',
                   token, Sys.getpid()))
  rm(token)
  fail_before_json <- function(message) {
    # Only fixed messages call this path, before jsonlite is available.
    ack <- readLines(control, n = 1L, warn = FALSE)
    if (length(ack)) {
      send_raw(sprintf(paste0('{"bridge_event":"exit","code":1,"figures":0,',
                              '"script_error":{"message":"%s"}}'), message))
    }
    quit(status = 1L)
  }
  # Apply the user-selected package library to this child before loading any
  # namespace, including jsonlite. Keep the existing interpreter defaults as
  # fallback and leave the caller's library and startup files untouched.
  if (length(args) == 12L) {
    lib <- tryCatch(normalizePath(args[[12L]], winslash = "/", mustWork = TRUE),
                    error = function(error) NULL)
    if (!is.null(lib)) .libPaths(c(lib, .libPaths()))
    if (!identical(.libPaths()[[1L]], lib)) {
      fail_before_json("The selected R package library is unavailable.")
    }
  }
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    fail_before_json("The selected R needs the jsonlite package.")
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
    stop("Owned R control could not authenticate.")
  }

  adapter <- new.env(parent = baseenv())
  captured <- tryCatch({
    if (!requireNamespace("ggplot2", quietly = TRUE) ||
        !requireNamespace("svglite", quietly = TRUE)) {
      stop("The selected R needs ggplot2 and svglite.")
    }
    sys.source(args[[7L]], envir = adapter)
    native <- dirname(args[[7L]])
    sys.source(file.path(native, "ggplot_colour_scale.R"), envir = adapter)
    sys.source(file.path(native, "ggplot_points.R"), envir = adapter)
    sys.source(file.path(native, "ggplot_bars.R"), envir = adapter)
    sys.source(file.path(native, "ggplot_ribbons.R"), envir = adapter)
    sys.source(file.path(native, "ggplot_axes.R"), envir = adapter)
    sys.source(file.path(native, "ggplot_frame.R"), envir = adapter)
    palettes <- jsonlite::fromJSON(file.path(dirname(native), "color_palettes.json"), simplifyVector = FALSE)
    probe <- file.path(args[[11L]], "cairo-device-check.pdf")
    device <- adapter$open_ggplot_pdf(frame_pt, probe)
    grDevices::dev.off(which = device)
    unlink(probe)
    user <- new.env(parent = globalenv())
    # Bind a named native object from the current invocation only.
    program <- parse(file = script, keep.source = TRUE, encoding = "UTF-8")
    send(list(bridge_event = "execution_started", execution_id = execution, script = args[[10L]]))
    eval(program, envir = user)
    if (!exists(object, envir = user, inherits = FALSE)) stop("The named ggplot object is absent.")
    plot <- get(object, envir = user, inherits = FALSE)
    adapter$capture_ggplot(plot, frame_pt, palettes)
  }, error = function(error) {
    send(list(bridge_event = "exit", code = 1L, figures = 0L,
              script_error = list(message = substr(conditionMessage(error), 1L, 2000L))))
    quit(status = 1L)
  })
  send(list(bridge_event = "barrier", reason = "script_end", stems = list(stem)))

  edits_from <- function(request) {
    if (!identical(request$stem, stem)) stop("Unknown ggplot object.")
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
           !(patch$prop %in% c("invert_x", "invert_y", "grid_x", "grid_y", "minor_visible",
                               adapter$ggplot_frame_visibility) && is.logical(patch$value)))) {
        stop("Invalid native R edit.")
      }
      edits[[patch$gid]][[patch$prop]] <- patch$value
    }
    edits
  }

  repeat {
    request <- receive()
    if (is.null(request)) return(invisible(NULL))
    if (!identical(request$protocol_version, 1L) || !is.character(request$request_id)) {
      stop("Invalid native worker envelope.")
    }
    if (request$cmd %in% c("terminate", "shutdown")) return(invisible(NULL))
    envelope <- request[c("protocol_version", "request_id", "worker_generation",
                          "render_revision")]
    answer <- tryCatch(
      switch(request$cmd,
        build = list(ok = TRUE, stems = setNames(list(list()), stem),
                     svg = captured$svg, source = "ggplot2",
                     manifest = adapter$ggplot_manifest(captured$state, stem),
                     ggplot2 = as.character(utils::packageVersion("ggplot2"))),
        render = {
          edits <- edits_from(request)
          list(ok = TRUE, svg = adapter$render_ggplot(captured$state, edits),
               manifest = adapter$ggplot_manifest(captured$state, stem, edits))
        },
        preview_png = {
          edits <- edits_from(request)
          payload <- request$payload
          paired <- isTRUE(payload$with_manifest)
          if (paired) {
            path <- tempfile(pattern = "preview-", tmpdir = args[[11L]], fileext = ".png")
          } else {
            tag <- payload$tag
            if (!is.character(tag) || length(tag) != 1L || is.na(tag) ||
                !grepl("^[A-Za-z0-9_-]+$", tag)) stop("Invalid native R preview tag.")
            path <- file.path(args[[11L]], paste0(stem, "__", tag, ".png"))
          }
          tryCatch({
            measured <- adapter$preview_ggplot_png(captured$state, edits, path, payload$width, paired)
            if (paired) {
              list(ok = TRUE, png = jsonlite::base64_enc(readBin(path, "raw", file.info(path)$size)),
                   manifest = adapter$ggplot_manifest(captured$state, stem, edits, measured))
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
        continue = list(ok = TRUE, released = TRUE),
        list(ok = FALSE, error = list(code = "unknown_cmd", message = "Unsupported R operation."))
      ),
      error = function(error) {
        list(ok = FALSE, error = list(code = "bad_request", message = conditionMessage(error)))
      }
    )
    send(c(envelope, list(execution_id = execution), answer))
    if (identical(request$cmd, "continue")) break
  }
  send(list(bridge_event = "exit", code = 0L, figures = 1L))
}

main()
