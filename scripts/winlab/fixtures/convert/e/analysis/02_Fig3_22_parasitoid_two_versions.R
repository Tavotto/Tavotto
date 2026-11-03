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



# ---------------- Figure 3 ----------------
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



# ---------------- Figure 3 ----------------
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



# ---------------- Figure 3 ----------------
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



# ---------------- Figure 3 ----------------
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



# ---------------- Figure 3 ----------------
sumdat8 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p8a <- ggplot(sumdat8, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p8b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 8b") + theme_ms()
p8c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig8 <- (p8a | p8b) / p8c + plot_annotation(tag_levels = "A")
ggsave("Fig8_two_versions.pdf", fig8, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat9 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p9a <- ggplot(sumdat9, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p9b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 9b") + theme_ms()
p9c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig9 <- (p9a | p9b) / p9c + plot_annotation(tag_levels = "A")
ggsave("Fig9_two_versions.pdf", fig9, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat10 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p10a <- ggplot(sumdat10, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p10b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 10b") + theme_ms()
p10c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig10 <- (p10a | p10b) / p10c + plot_annotation(tag_levels = "A")
ggsave("Fig10_two_versions.pdf", fig10, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat11 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p11a <- ggplot(sumdat11, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p11b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 11b") + theme_ms()
p11c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig11 <- (p11a | p11b) / p11c + plot_annotation(tag_levels = "A")
ggsave("Fig11_two_versions.pdf", fig11, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat12 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p12a <- ggplot(sumdat12, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p12b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 12b") + theme_ms()
p12c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig12 <- (p12a | p12b) / p12c + plot_annotation(tag_levels = "A")
ggsave("Fig12_two_versions.pdf", fig12, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat13 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p13a <- ggplot(sumdat13, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p13b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 13b") + theme_ms()
p13c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig13 <- (p13a | p13b) / p13c + plot_annotation(tag_levels = "A")
ggsave("Fig13_two_versions.pdf", fig13, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat14 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p14a <- ggplot(sumdat14, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p14b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 14b") + theme_ms()
p14c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig14 <- (p14a | p14b) / p14c + plot_annotation(tag_levels = "A")
ggsave("Fig14_two_versions.pdf", fig14, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat15 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p15a <- ggplot(sumdat15, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p15b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 15b") + theme_ms()
p15c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig15 <- (p15a | p15b) / p15c + plot_annotation(tag_levels = "A")
ggsave("Fig15_two_versions.pdf", fig15, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat16 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p16a <- ggplot(sumdat16, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p16b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 16b") + theme_ms()
p16c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig16 <- (p16a | p16b) / p16c + plot_annotation(tag_levels = "A")
ggsave("Fig16_two_versions.pdf", fig16, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat17 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p17a <- ggplot(sumdat17, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p17b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 17b") + theme_ms()
p17c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig17 <- (p17a | p17b) / p17c + plot_annotation(tag_levels = "A")
ggsave("Fig17_two_versions.pdf", fig17, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat18 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p18a <- ggplot(sumdat18, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p18b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 18b") + theme_ms()
p18c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig18 <- (p18a | p18b) / p18c + plot_annotation(tag_levels = "A")
ggsave("Fig18_two_versions.pdf", fig18, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat19 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p19a <- ggplot(sumdat19, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p19b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 19b") + theme_ms()
p19c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig19 <- (p19a | p19b) / p19c + plot_annotation(tag_levels = "A")
ggsave("Fig19_two_versions.pdf", fig19, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat20 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p20a <- ggplot(sumdat20, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p20b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 20b") + theme_ms()
p20c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig20 <- (p20a | p20b) / p20c + plot_annotation(tag_levels = "A")
ggsave("Fig20_two_versions.pdf", fig20, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat21 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p21a <- ggplot(sumdat21, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p21b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 21b") + theme_ms()
p21c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig21 <- (p21a | p21b) / p21c + plot_annotation(tag_levels = "A")
ggsave("Fig21_two_versions.pdf", fig21, width = 9, height = 7)



# ---------------- Figure 3 ----------------
sumdat22 <- para %>% group_by(version, species) %>%
  summarise(m = mean(attack_rate), e = se(attack_rate), n = n(), .groups = "drop")
p22a <- ggplot(sumdat22, aes(x = species, y = m, fill = version)) +
  geom_col(position = position_dodge(0.8), width = 0.7) +
  geom_errorbar(aes(ymin = m - e, ymax = m + e), position = position_dodge(0.8), width = 0.25) +
  scale_fill_manual(values = pal2) + labs(x = NULL, y = "攻击率", fill = "版本") + theme_ms()
p22b <- ggplot(para, aes(x = host_density, y = attack_rate, colour = version)) +
  geom_point(alpha = 0.5, size = 1) + geom_smooth(method = "loess", se = TRUE, linewidth = 0.8) +
  scale_colour_manual(values = pal2) + scale_x_continuous(breaks = pretty_breaks(6)) +
  labs(x = "寄主密度", y = "攻击率", title = "Fig 22b") + theme_ms()
p22c <- ggplot(field, aes(x = year, y = abundance, group = site, colour = site)) +
  geom_line() + geom_point(size = 1) + facet_wrap(~site, nrow = 1, scales = "free_y") +
  theme_ms() + theme(legend.position = "none", axis.text.x = element_text(angle = 45, hjust = 1))
fig22 <- (p22a | p22b) / p22c + plot_annotation(tag_levels = "A")
ggsave("Fig22_two_versions.pdf", fig22, width = 9, height = 7)


