# The coordinate method receives titles in their displayed side order,
# after ggplot2 has resolved guide names, scale names and labellers.
ggplot_relabel_coordinates <- function(coord, titles) {
  sides <- c("xlab-t", "xlab-b", "ylab-l", "ylab-r")
  changes <- intersect(sides, names(titles))
  if (!length(changes)) return(coord)
  force(coord)
  force(titles)
  ggplot_clone(coord, labels = function(self, labels, panel_params) {
    ordered <- ggplot2::ggproto_parent(coord, self)$labels(labels, panel_params)
    for (side in changes) {
      index <- match(side, sides)
      axis <- if (index <= 2L) "x" else "y"
      ordered[[axis]][[(index - 1L) %% 2L + 1L]] <- titles[[side]]
    }
    ordered
  })
}

ggplot_relabel_titles <- function(plot, titles) {
  for (name in intersect(names(titles), c("title", "subtitle", "caption"))) {
    plot$labels[[name]] <- titles[[name]]
  }
  plot
}
