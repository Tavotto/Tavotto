ggplot_minor_source_guide <- function(guide, params, style) {
  guide <- ggplot_guide_with_minor_labels(guide, params)
  if (!isTRUE(style$minor_visible)) return(guide)
  ggplot_clone(guide, setup_elements = function(params, elements, theme) {
    resolved <- guide$setup_elements(params, elements, theme)
    if (!ggplot_minor_marks_drawn(resolved)) {
      stock <- ggplot2::GuideAxis
      defaults <- stock$setup_elements(stock$setup_params(params), stock$elements, ggplot2::theme_grey())
      resolved[c("minor", "minor_length")] <- defaults[c("minor", "minor_length")]
    }
    resolved
  })
}

ggplot_minor_source_panel <- function(panel, styles, coord) {
  guides <- panel$guides
  params <- guides$params
  drawn <- guides$guides
  for (axis in names(styles)) {
    index <- which(vapply(params, function(param) identical(param$aesthetic, axis) &&
                           identical(param$position, panel[[axis]]$position), logical(1L)))
    if (length(index) != 1L || !identical(class(drawn[[index]])[[1L]], "GuideAxis")) {
      stop("Tavotto could not find the R minor tick guide.")
    }
    minor <- ggplot_minor_spacing(panel[[axis]])
    style <- styles[[axis]]
    edited <- ggplot_edit_minor_ticks(panel, params[[index]], list(axis = axis, minor = minor), style, coord)
    panel <- edited$panel
    params[[index]] <- ggplot_format_minor_labels(panel, edited$params, style$minor_format)
    drawn[[index]] <- ggplot_minor_source_guide(drawn[[index]], params[[index]], style)
  }
  panel$guides <- ggplot_clone(guides, params = params, guides = drawn)
  panel
}

ggplot_minor_source_panels <- function(layout, recipes) {
  changes <- list()
  for (recipe in recipes) {
    axis <- recipe$axis
    id <- ggplot_facet_group_id(layout$layout, axis, recipe$facet)
    if (is.null(id) || is.na(id)) stop("Tavotto could not match the R minor tick axis.")
    field <- paste0("SCALE_", toupper(axis))
    for (row in which(layout$layout[[field]] == id)) {
      panel <- as.character(layout$layout$PANEL[[row]])
      changes[[panel]][[axis]] <- utils::modifyList(changes[[panel]][[axis]] %||% list(), recipe$style)
    }
  }
  for (panel in names(changes)) {
    i <- as.integer(panel)
    layout$panel_params[[i]] <- ggplot_minor_source_panel(layout$panel_params[[i]], changes[[panel]], layout$coord)
  }
}

ggplot_install_minor_locators <- function(layout, recipes) {
  if (length(layout$tavotto_minor_recipes)) {
    return(ggplot_clone(layout, tavotto_minor_recipes = recipes))
  }
  if (!identical(as.list(layout)$setup_panel_guides, as.list(ggplot2::Layout)$setup_panel_guides)) {
    stop("Tavotto minor tick locators need the original R panel guides.")
  }
  force(layout)
  ggplot_clone(layout, tavotto_minor_recipes = recipes, tavotto_minor_install = ggplot_install_minor_locators,
               tavotto_minor_native_guides = as.list(layout)$setup_panel_guides,
               setup_panel_guides = function(self, guides, layers) {
    ggplot2::ggproto_parent(layout, self)$setup_panel_guides(guides, layers)
    if (!isTRUE(self$tavotto_skip_minor_locators)) ggplot_minor_source_panels(self, self$tavotto_minor_recipes)
  })
}

ggplot_with_minor_locators <- function(plot, recipes) {
  plot$layout <- ggplot_install_minor_locators(plot$layout, c(plot$layout$tavotto_minor_recipes, recipes))
  plot
}
