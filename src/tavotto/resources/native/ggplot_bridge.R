# Owned R process. User output stays on stdout and stderr. Control uses the
# existing local bridge envelope on a separate authenticated connection.

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  token <- Sys.getenv("TAVOTTO_BRIDGE_TOKEN")
  Sys.unsetenv("TAVOTTO_BRIDGE_TOKEN")
  if (!nzchar(token) || length(args) != 7L) stop("Invalid owned R launch.")

  script <- args[[1L]]
  object <- args[[2L]]
  frame_pt <- as.numeric(args[3:4])
  control <- socketConnection(args[[5L]], as.integer(args[[6L]]),
                              blocking = TRUE, open = "r+", timeout = 3600)
  on.exit(close(control))
  send <- function(frame) {
    writeLines(jsonlite::toJSON(frame, auto_unbox = TRUE, null = "null",
                                digits = NA), control, useBytes = TRUE)
    flush(control)
  }
  receive <- function() {
    line <- readLines(control, n = 1L, warn = FALSE, encoding = "UTF-8")
    if (length(line) == 0L) return(NULL)
    jsonlite::fromJSON(line, simplifyVector = FALSE)
  }
  send(list(bridge_hello = 1L, protocol_version = 1L, token = token, pid = Sys.getpid()))
  rm(token)
  ack <- receive()
  if (!isTRUE(ack$ok) || !identical(ack$bridge_hello, 1L)) {
    stop("Owned R control could not authenticate.")
  }

  if (!requireNamespace("ggplot2", quietly = TRUE) ||
      !requireNamespace("svglite", quietly = TRUE)) {
    stop("The selected R needs ggplot2 and svglite.")
  }
  adapter <- new.env(parent = baseenv())
  sys.source(args[[7L]], envir = adapter)
  user <- new.env(parent = globalenv())
  # Bind a named native object from this invocation only, never from R's
  # search path or a prior session.
  sys.source(script, envir = user)
  plot <- get(object, envir = user, inherits = FALSE)
  captured <- adapter$capture_ggplot(plot, frame_pt)
  send(list(bridge_event = "barrier", reason = "script_end", stems = list(object)))

  color_from <- function(request) {
    if (!identical(request$stem, object)) stop("Unknown ggplot object.")
    patches <- request$payload$patches
    if (!is.list(patches)) stop("Expected a full list of plot patches.")
    color <- NULL
    for (patch in patches) {
      if (!identical(sort(names(patch)), c("gid", "prop", "value")) ||
          !identical(patch$gid, "plot.title") || !identical(patch$prop, "color") ||
          !is.character(patch$value)) {
        stop("This ggplot adapter supports the plain-text title color.")
      }
      color <- patch$value
    }
    color
  }

  repeat {
    request <- receive()
    if (is.null(request)) break
    if (!identical(request$protocol_version, 1L) || !is.character(request$request_id)) {
      stop("Invalid native worker envelope.")
    }
    if (request$cmd %in% c("terminate", "shutdown")) break
    envelope <- request[c("protocol_version", "request_id", "worker_generation",
                          "render_revision")]
    answer <- tryCatch(
      switch(request$cmd,
        build = list(ok = TRUE, stems = setNames(list(list()), object),
                     svg = captured$svg, source = "ggplot2",
                     manifest = adapter$ggplot_manifest(captured$state, object),
                     ggplot2 = as.character(utils::packageVersion("ggplot2"))),
        render = {
          color <- color_from(request)
          list(ok = TRUE, svg = adapter$render_ggplot(captured$state, color),
               manifest = adapter$ggplot_manifest(captured$state, object, color))
        },
        export = {
          color <- color_from(request)
          payload <- request$payload
          if (!identical(payload$format, "pdf") || !is.character(payload$path) ||
              !is.null(payload$svg_text_path)) {
            stop("The ggplot adapter exports a PDF with an optional SVG companion.")
          }
          adapter$export_ggplot_pdf(captured$state, color, payload$path)
          if (!is.null(payload$svg_path)) {
            writeLines(adapter$render_ggplot(captured$state, color), payload$svg_path, useBytes = TRUE)
          }
          list(ok = TRUE, path = payload$path)
        },
        continue = list(ok = TRUE, released = TRUE),
        list(ok = FALSE, error = list(code = "unknown_cmd", message = "Unsupported R operation."))
      ),
      error = function(error) {
        list(ok = FALSE, error = list(code = "bad_request", message = conditionMessage(error)))
      }
    )
    send(c(envelope, answer))
    if (identical(request$cmd, "continue")) break
  }
}

main()
