# 工具函数
se <- function(x) sd(x) / sqrt(length(x))
theme_ms <- function() {
  theme_classic(base_size = 10) + theme(legend.position = "top", axis.line = element_line(linewidth = 0.4))
}
pal2 <- c(v1 = "#E69F00", v2 = "#56B4E9")
