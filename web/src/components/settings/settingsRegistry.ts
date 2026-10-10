import { t as translate } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'

/**
 * 设置的**搜索注册表**（2026-10-07 设计审计 §9.1，学 OpenBitFun `settingsRegistry.ts`）：每个分区与分区里
 * 能搜到的行登记一条——名字取页面上那一行**同一个 i18n key**（不另写同义词，`settings-shell-and-packages.md`
 * 「设置页与它深链过去的对话框读同一批 key」同一条纪律），再加一串按语言给的关键词（`settings.search.kw.*`，
 * 用户会搜、但标签里没有的词：「dpi」「pip」「遥测」）。
 *
 * 页面上对应的那一行带 `data-settings-anchor="<id>"`：点搜索结果 = 切到那一页、把那一行滚进视野并闪一下。
 * 锚点只在这张表与页面里出现，**不是 e2e 锚点**（用例另有自己的 data-*）。
 *
 * 搜索纯本地、只比对文字（与包管理页「打字不出网」同一条纪律），不发任何请求。
 */

export type SectionId =
  | 'general'
  | 'project'
  | 'style'
  | 'spec'
  | 'export'
  // 分区 **id** 仍是 'ai'（AiPanel 的「打开设置」按它跳转，改名等于断掉那条路径）
  | 'ai'
  | 'packages'
  | 'diagnostics'
  | 'about'

export interface SettingsEntry {
  /** 页面上 `data-settings-anchor` 的值 */
  id: string
  section: SectionId
  /** 与页面上那一行同一个文案 */
  label: () => string
  /** 额外关键词（按界面语言给；空格分隔） */
  keywords?: () => string
  /**
   * 落点：页面上按顺序找这几个 `data-settings-anchor`，第一个找得到的就是它（缺省 = `[id]`）。
   * 只在某些状态下才渲染的行（项目环境、跟随更新、取数之后的助手清单）退到一定在场的那一组上，
   * 点了结果不会落空。
   */
  anchors?: readonly string[]
}

/** 一条结果在页面上要找的锚点（按顺序） */
export const anchorsOf = (e: SettingsEntry): readonly string[] => e.anchors ?? [e.id]

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })
const pf = (key: string) => translate(`profiles.${key}`, { ns: 'dialogs' })
const ex = (key: string) => translate(`export.${key}`, { ns: 'dialogs' })
const en = (key: string) => translate(`engine.${key}`, { ns: 'errors' })

/** 键写成字面量（i18n 死键门禁按「源码里出现过这个串」判活） */
export const SETTINGS_REGISTRY: readonly SettingsEntry[] = [
  // ---- 通用 ----
  { id: 'general.theme', section: 'general', label: () => st('general.theme'), keywords: () => st('search.kw.theme') },
  { id: 'general.language', section: 'general', label: () => st('general.language'), keywords: () => st('search.kw.language') },
  { id: 'general.layout', section: 'general', label: () => st('general.layout'), keywords: () => st('search.kw.layout') },
  { id: 'general.shortcuts', section: 'general', label: () => st('shortcuts.label'), keywords: () => st('search.kw.shortcuts') },
  { id: 'general.leftPinned', section: 'general', label: () => st('sidebars.leftPinned') },
  { id: 'general.rightPinned', section: 'general', label: () => st('sidebars.rightPinned') },
  { id: 'general.dragCompanions', section: 'general', label: () => st('canvas.dragCompanions') },
  { id: 'general.canvas', section: 'general', label: () => st('canvas.more'), keywords: () => st('search.kw.canvas') },
  { id: 'general.tutorial', section: 'general', label: () => st('tutorial.label'), keywords: () => st('search.kw.tutorial') },
  // ---- 项目 ----
  { id: 'project.current', section: 'project', label: () => st('project.current') },
  { id: 'project.scripts', section: 'project', label: () => st('project.scripts'), keywords: () => st('search.kw.scripts') },
  { id: 'project.python', section: 'project', label: () => st('project.python'), keywords: () => st('search.kw.python'), anchors: ['project.python', 'project.runtime'] },
  { id: 'project.workdir', section: 'project', label: () => en('workdirLabel'), anchors: ['project.workdir', 'project.runtime'] },
  { id: 'project.exportDir', section: 'project', label: () => st('project.exportDir') },
  { id: 'project.backupDir', section: 'project', label: () => st('project.backupDir') },
  { id: 'project.writeBack', section: 'project', label: () => st('project.allowWriteBack'), keywords: () => st('search.kw.writeBack') },
  // ---- 样式 / 规范 ----
  { id: 'style.library', section: 'style', label: () => pf('library.style') },
  { id: 'style.preview', section: 'style', label: () => pf('previewTitle'), anchors: ['style.preview', 'style.library'] },
  { id: 'spec.inUse', section: 'spec', label: () => pf('binding.current'), keywords: () => st('search.kw.spec') },
  { id: 'spec.follow', section: 'spec', label: () => pf('follow'), anchors: ['spec.follow', 'spec.inUse'] },
  { id: 'spec.library', section: 'spec', label: () => pf('library.spec') },
  // ---- 导出 ----
  { id: 'export.formats', section: 'export', label: () => st('export.defaultFormats'), keywords: () => st('search.kw.formats') },
  { id: 'export.ppi', section: 'export', label: () => ex('ppiLabel'), keywords: () => st('search.kw.ppi') },
  { id: 'export.report', section: 'export', label: () => ex('reportToggle') },
  // ---- 改图助手 ----
  { id: 'ai.default', section: 'ai', label: () => st('agents.defaultLabel'), keywords: () => st('search.kw.agents'), anchors: ['ai.default', 'ai.agents'] },
  { id: 'ai.codex', section: 'ai', label: () => st('agents.codexIntegrationName', { product: PRODUCT_NAME }) },
  // ---- Python 库 ----
  { id: 'packages.env', section: 'packages', label: () => st('packages.envTitle', { product: PRODUCT_NAME }), anchors: ['packages.env', 'packages.install'] },
  { id: 'packages.install', section: 'packages', label: () => st('packages.userTitle'), keywords: () => st('search.kw.packages') },
  { id: 'packages.builtin', section: 'packages', label: () => st('packages.builtinTitle') },
  // ---- 帮助与诊断 ----
  { id: 'diagnostics.health', section: 'diagnostics', label: () => st('diagnostics.healthTitle') },
  { id: 'diagnostics.report', section: 'diagnostics', label: () => st('diagnostics.reportTitle'), keywords: () => st('search.kw.diagnostics') },
  { id: 'diagnostics.perf', section: 'diagnostics', label: () => st('diagnostics.perfRow'), anchors: ['diagnostics.perf', 'diagnostics.dev'] },
  // ---- 关于与更新 ----
  { id: 'about.updates', section: 'about', label: () => st('update.check'), keywords: () => st('search.kw.updates') },
  { id: 'about.telemetry', section: 'about', label: () => st('about.telemetry.title'), keywords: () => st('search.kw.telemetry') },
]

/** 归一：小写、去掉首尾空白；中英文都按子串比 */
const norm = (s: string) => s.toLowerCase().trim()

export interface SettingsSearchResult {
  section: SectionId
  /** 分区名本身就命中了 */
  sectionHit: boolean
  entries: SettingsEntry[]
}

/**
 * 按查询词过滤：命中分区名 → 整页算命中；命中某一行的名字或关键词 → 那一行算命中。
 * 多个词（空格分开）要**全部**落在「行名 + 关键词」（或再加上分区名）里才算。返回值按 `order`（导航顺序）排。
 */
export function searchSettings(
  query: string,
  order: readonly SectionId[],
  sectionLabel: (id: SectionId) => string,
): SettingsSearchResult[] {
  const terms = norm(query).split(/\s+/).filter(Boolean)
  if (!terms.length) return []
  const all = (hay: string) => terms.every((t) => hay.includes(t))
  const out: SettingsSearchResult[] = []
  for (const section of order) {
    const name = norm(sectionLabel(section))
    const sectionHit = all(name)
    // 分区名自己就命中时，不把这一页的每一行都列出来（那等于没过滤）；词分落在分区名与行名上
    // （「导出 分辨率」）时才借分区名凑齐
    const entries = SETTINGS_REGISTRY.filter((e) => {
      if (e.section !== section) return false
      const row = `${norm(e.label())} ${norm(e.keywords?.() ?? '')}`
      return all(row) || (!sectionHit && all(`${name} ${row}`))
    })
    if (sectionHit || entries.length) out.push({ section, sectionHit, entries })
  }
  return out
}
