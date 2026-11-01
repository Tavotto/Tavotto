# ============ 寄生蜂两个版本的对比图 (Fig 3-7) ============
library(ggplot2)
library(dplyr)
library(patchwork)
library(scales)

source("../R/utils.R")

# 读入数据（相对上一级目录）
para <- read.csv("../data/parasitoid.csv")
field <- read.csv("../data/field.csv")

# ---------------- Figure 3 ----------------
sumdat3 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p3a <- ggplot(sumdat3, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p3b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 3b") + theme_ms()
p3c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig3 <- (p3a | p3b) / p3c + plot_annotation(tag_levels = "A")
ggsave("Fig3_two_versions.pdf", fig3, width = 9, height = 7)

# ---------------- Figure 4 ----------------
sumdat4 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p4a <- ggplot(sumdat4, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p4b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 4b") + theme_ms()
p4c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig4 <- (p4a | p4b) / p4c + plot_annotation(tag_levels = "A")
ggsave("Fig4_two_versions.pdf", fig4, width = 9, height = 7)

# ---------------- Figure 5 ----------------
sumdat5 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p5a <- ggplot(sumdat5, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p5b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 5b") + theme_ms()
p5c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig5 <- (p5a | p5b) / p5c + plot_annotation(tag_levels = "A")
ggsave("Fig5_two_versions.pdf", fig5, width = 9, height = 7)

# ---------------- Figure 6 ----------------
sumdat6 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p6a <- ggplot(sumdat6, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p6b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 6b") + theme_ms()
p6c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig6 <- (p6a | p6b) / p6c + plot_annotation(tag_levels = "A")
ggsave("Fig6_two_versions.pdf", fig6, width = 9, height = 7)

# ---------------- Figure 7 ----------------
sumdat7 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p7a <- ggplot(sumdat7, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p7b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 7b") + theme_ms()
p7c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig7 <- (p7a | p7b) / p7c + plot_annotation(tag_levels = "A")
ggsave("Fig7_two_versions.pdf", fig7, width = 9, height = 7)

