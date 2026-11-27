# Prepended to R's system profile in the disposable Windows replay copy.
# AppContainer can open staged files but cannot query their DOS volume name.
# R's non-strict native normalizer still resolves the absolute spelling.
normalizePath <- local({
  native <- normalizePath
  slash <- function(path) chartr("\\", "/", path)
  root <- tolower(slash(Sys.getenv("TAVOTTO_R_REPLAY_ROOT")))
  prefix <- paste0(root, "/")

  function(path, winslash = "\\", mustWork = NA) {
    if (!nzchar(root) || identical(mustWork, FALSE)) return(native(path, winslash, mustWork))
    absolute <- native(path, winslash, mustWork = FALSE)
    candidate <- tolower(slash(absolute))
    # Only accept an existing path inside the already protected copy.
    # All other paths retain R's normal errors and warning semantics.
    if (all(!is.na(candidate) & (candidate == root | startsWith(candidate, prefix))) &&
        all(file.exists(absolute))) return(absolute)
    native(path, winslash, mustWork)
  }
})
