ggplot_transform_symlog <- function() {
  # Match the Matplotlib symlog preset used by the shared scale control.
  base <- 10
  threshold <- 2
  slope <- 1 / (1 - 1 / base)
  scales::new_transform("tavotto-symlog", transform = function(values) {
    result <- values * slope
    outside <- which(abs(values) > threshold)
    tail <- values[outside]
    result[outside] <- sign(tail) * threshold * (slope + log(abs(tail) / threshold, base))
    result
  }, inverse = function(values) {
    result <- values / slope
    outside <- which(abs(values) > threshold * slope)
    tail <- values[outside]
    result[outside] <- sign(tail) * threshold * base^(abs(tail) / threshold - slope)
    result
  })
}
