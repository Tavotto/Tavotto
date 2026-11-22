# Owned R process. User output stays on stdout and stderr. Control uses the
# existing local bridge envelope on a separate authenticated connection.

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  token <- Sys.getenv("TAVOTTO_BRIDGE_TOKEN")
  Sys.unsetenv("TAVOTTO_BRIDGE_TOKEN")
  if (!grepl("^[A-Za-z0-9_-]+$", token) || length(args) != 11L) {
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
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    # The hello is the only request before dependency checks. The diagnostic
    # is fixed text so it can also be reported without the JSON package.
    ack <- readLines(control, n = 1L, warn = FALSE)
    if (length(ack)) {
      send_raw('{"bridge_event":"exit","code":1,"figures":0,"script_error":{"message":"The selected R needs the jsonlite package."}}')
    }
    quit(status = 1L)
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
    adapter$capture_ggplot(plot, frame_pt)
  }, error = function(error) {
    send(list(bridge_event = "exit", code = 1L, figures = 0L,
              script_error = list(message = substr(conditionMessage(error), 1L, 2000L))))
    quit(status = 1L)
  })
  send(list(bridge_event = "barrier", reason = "script_end", stems = list(stem)))

  text_colors_from <- function(request) {
    if (!identical(request$stem, stem)) stop("Unknown ggplot object.")
    patches <- request$payload$patches
    if (!is.list(patches)) stop("Expected a full list of plot patches.")
    colors <- list()
    for (patch in patches) {
      if (!is.list(patch) ||
          !identical(sort(setdiff(names(patch), "identity")), c("gid", "prop", "value")) ||
          !is.character(patch$gid) || length(patch$gid) != 1L ||
          is.na(patch$gid) || !nzchar(patch$gid) ||
          !identical(patch$prop, "color") || !is.character(patch$value)) {
        stop("This ggplot adapter supports RGB colors for supported text targets.")
      }
      colors[[patch$gid]] <- patch$value
    }
    colors
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
          colors <- text_colors_from(request)
          list(ok = TRUE, svg = adapter$render_ggplot(captured$state, colors),
               manifest = adapter$ggplot_manifest(captured$state, stem, colors))
        },
        export = {
          colors <- text_colors_from(request)
          payload <- request$payload
          if (!identical(payload$format, "pdf") || !is.character(payload$path)) {
            stop("The ggplot adapter exports a PDF with an optional SVG companion.")
          }
          adapter$export_ggplot_pdf(captured$state, colors, payload$path)
          companion <- NULL
          if (!is.null(payload$svg_path)) {
            companion <- adapter$render_ggplot(captured$state, colors)
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
