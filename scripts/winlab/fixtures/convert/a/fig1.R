library(ggplot2)
library(dplyr)

dat <- read.csv("data/growth.csv")
dat$strain <- factor(dat$strain, levels = c("WT", "mutA", "mutB"))

theme_pub <- function(base_size = 11) {
  theme_bw(base_size = base_size) +
    theme(panel.grid.minor = element_blank(),
          strip.background = element_rect(fill = "grey92", colour = NA),
          strip.text = element_text(face = "bold"),
          legend.position = "bottom",
          axis.title = element_text(size = base_size + 1))
}

p <- ggplot(dat, aes(x = time, y = mean, colour = condition, group = condition)) +
  geom_line(linewidth = 0.7) +
  geom_point(size = 1.6) +
  geom_errorbar(aes(ymin = mean - sd, ymax = mean + sd), width = 0.4) +
  facet_wrap(~strain, ncol = 3) +
  scale_colour_manual(values = c(ctrl = "#4C72B0", drug = "#DD8452", heat = "#55A868")) +
  labs(x = "Time (h)", y = "OD600", colour = "Condition", title = "Growth curves") +
  theme_pub()

ggsave("fig.pdf", p, width = 7.2, height = 3, units = "in")
