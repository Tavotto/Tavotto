import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { dialogCovered, useUiStore } from '@/store/uiStore'
import { askDiscardDraft } from './askDiscardDraft'
import { Dialog } from './ui/Dialog'
import { listRowClass } from './ui/listRow'
import { ChevronRight } from './ui/icons'
import { ICON_SIZE } from './ui/Icon'
import { SearchInput } from './ui/SearchInput'
import { CodingAgentsSection } from './settings/CodingAgentsSection'
import { DiagnosticsSettings } from './settings/DiagnosticsSettings'
import { ExportSettings } from './settings/ExportSettings'
import { GeneralSettings } from './settings/GeneralSettings'
import { PackagesSettings } from './settings/PackagesSettings'
import { PrivacyAboutSettings } from './settings/PrivacyAboutSettings'
import { ProfilesSettings } from './settings/ProfilesSettings'
import { ProjectSettings } from './settings/ProjectSettings'
import { SettingsCrumbContext, type SettingsCrumb } from './settings/settingsPageContext'
import { anchorsOf, searchSettings, type SectionId } from './settings/settingsRegistry'

export type { SectionId } from './settings/settingsRegistry'

/**
 * 设置对话框的**外壳**：导航（含搜索）+ 页头 + 分区分派，仅此而已（ADR 0038）。
 *
 * 外壳合同（`UX_CONTRACTS.md` 5d；形态见 Design Constitution 第十二节）：
 *   * **尺寸固定**：宽 `SHELL_WIDTH`、高 `SHELL_HEIGHT`（上限 86vh / 视口宽减
 *     2rem，小窗口按它收缩），切分区时外框一个像素都不动；内容区自己滚，标题与
 *     导航固定；
 *   * **内容是一列居中的 `CONTENT_MAX_WIDTH`**（2026-10-07 设计审计 §9.1：此前 640 靠左，可用约 760，
 *     右侧空出 120px，控件浮在对话框中部）；要铺满的分区声明 `wide`（`CONTENT_MODE`），不由每一页自己决定；
 *   * **每页一个页头**：`type-heading` 17 / 600 的页名 + 一句说明（宪法第十三节 2026-10-07 修订：钻入页与
 *     长页滚动后不再丢上下文）；钻入页（改图助手 › Codex）把页头换成面包屑（`useSettingsCrumb`）；
 *   * **导航**：200px 一列，顶上一个 28px 搜索框（`settingsRegistry` 的名字 + 关键词，纯本地过滤，点结果跳到
 *     那一页那一行）；项 30px / 8 圆角 / 13px，选中 = 轻 tint + 600；组名 12 / 500 / ink-3；
 *   * **小窗口 / 大缩放**：<640px 时导航从左栏变成顶部一行可横滚的分区条（搜索框藏起来），
 *     内容区仍独立滚动，绝不横向溢出；
 *   * **切页策略**：内容区滚回顶部、焦点留在导航（用户在导航），↑ ↓ Home End
 *     在导航里走、Enter / Space 选中；新页淡入（只有进场、没有退场，减弱动效时不播）；
 *   * desktop / browser 复用同一个外壳——这里没有任何平台分支。
 *
 * 每个分区各住一个文件（`components/settings/`）。行与帮助的基础构件在
 * `settings/SettingRow.tsx`，分组是 `ui/FormSection` + `ui/FieldGroup`。
 */

export const SECTIONS: SectionId[] = [
  'general',
  'project',
  'style',
  'spec',
  'export',
  'ai',
  'packages',
  'diagnostics',
  'about',
]

/**
 * 旧分区 id → 新分区。深链的调用方（导出面板 / 素材库 / AiPanel）与用户的
 * 肌肉记忆都可能还带着旧名字；不认识的一律回到「通用」而不是白屏。
 */
const ALIASES: Record<string, SectionId> = {
  profiles: 'spec',
  // 十一页并九页（2026-09-30）：「界面」并进「通用」、「更新」并进「关于与更新」
  interface: 'general',
  canvas: 'general',
  sidebars: 'general',
  shortcuts: 'general',
  update: 'about',
}

export function resolveSection(requested: string | null | undefined): SectionId | null {
  if (!requested) return null
  if ((SECTIONS as string[]).includes(requested)) return requested as SectionId
  return ALIASES[requested] ?? null
}

/**
 * 外壳尺寸（一个出处；e2e 与 vitest 按它量）。成熟桌面设置窗口的那一档：
 * 1000×680。Dialog 自带 `max-w-[calc(100vw-2rem)]` / `max-h-[86vh]`，小窗口
 * （1024×640、150% 缩放）上由它收缩，外框永远在视口内、四边留 1rem。
 */
export const SHELL_WIDTH = 1000
/** 固定高；小屏上由 Dialog 的 86vh 上限收缩 */
export const SHELL_HEIGHT = '680px'
/**
 * 普通分区的内容列宽（px），在内容区里居中（2026-10-07 设计审计 §9.1：640 → 680）。
 * 1000 的外壳减去 200 的导航与左右各 24 的内边距，可用约 752——680 留出两侧呼吸，又让一行设置
 * （标题列 + 240 的控件列）不至于读不成一列。
 */
export const CONTENT_MAX_WIDTH = 680

/**
 * 导航分组：只影响视觉（组间距 + 一行 type-section 组名），`SECTIONS` 的顺序与
 * 键盘遍历顺序不变。组名文案 `settings.navGroup.*`。
 */
export const NAV_GROUPS: { id: 'general' | 'workflow' | 'integrations' | 'system'; sections: SectionId[] }[] = [
  { id: 'general', sections: ['general', 'project'] },
  { id: 'workflow', sections: ['style', 'spec', 'export'] },
  { id: 'integrations', sections: ['ai', 'packages'] },
  { id: 'system', sections: ['diagnostics', 'about'] },
]

/**
 * 每个分区的内容宽度模式。`normal`：一列居中、最大 `CONTENT_MAX_WIDTH`；`wide`：
 * 铺满内容区（只剩包管理的表格）。不给每一页自己随意布局。
 */
export const CONTENT_MODE: Record<SectionId, 'normal' | 'wide'> = {
  general: 'normal',
  project: 'normal',
  // 样式 / 规范：库收成一行之后不再需要左清单右编辑器的宽页（2026-09-15 打磨批次 B）
  style: 'normal',
  spec: 'normal',
  export: 'normal',
  ai: 'normal',
  packages: 'wide',
  diagnostics: 'normal',
  about: 'normal',
}

/** 本对话框的文案在 dialogs:settings.* 下 */
const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 页头那句说明：键写成字面量表（i18n 死键门禁按「源码里出现过这个串」判活） */
const PAGE_DESC: Record<SectionId, () => string> = {
  general: () => st('pageDesc.general'),
  project: () => st('pageDesc.project'),
  style: () => st('pageDesc.style'),
  spec: () => st('pageDesc.spec'),
  export: () => st('pageDesc.export'),
  ai: () => st('pageDesc.ai'),
  packages: () => st('pageDesc.packages'),
  diagnostics: () => st('pageDesc.diagnostics'),
  about: () => st('pageDesc.about'),
}

/** 点了搜索结果之后，那一行亮多久（ms）——只是「在这儿」的提示，不是状态 */
const SEARCH_HIT_MS = 1600

/**
 * 搜索框此刻看得见吗？**同源对**：与搜索框外层的 `hidden … sm:block`（Tailwind `sm` = 40rem）是同一条判据。
 * 没有 `matchMedia` 的环境当作宽屏（探测不到不该把搜索整个关掉）。
 */
const SEARCH_SHOWN_QUERY = '(min-width: 40rem)'
function useSearchShown(): boolean {
  const [shown, setShown] = useState(
    () => typeof matchMedia === 'undefined' || matchMedia(SEARCH_SHOWN_QUERY).matches,
  )
  useEffect(() => {
    if (typeof matchMedia === 'undefined') return
    const mql = matchMedia(SEARCH_SHOWN_QUERY)
    const sync = () => setShown(mql.matches)
    sync()
    mql.addEventListener?.('change', sync)
    return () => mql.removeEventListener?.('change', sync)
  }, [])
  return shown
}

export function SettingsDialog() {
  const { i18n } = useTranslation('dialogs')
  const open = useUiStore((s) => s.settingsOpen)
  const setOpen = useUiStore((s) => s.setSettingsOpen)
  const requested = useUiStore((s) => s.settingsSection)
  // 上面还压着论文样式对话框时整层藏起来（状态不丢），它关掉就回来
  const covered = useUiStore((s) => dialogCovered(s.dialogStack, 'settings'))
  const [section, setSection] = useState<SectionId>('general')
  const navRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const searchShown = useSearchShown()
  const hitTimers = useRef(new Set<number>())
  /** 点了某个搜索结果：切到那一页之后要滚到的锚点（按顺序找，第一个在场的） */
  const [pendingAnchor, setPendingAnchor] = useState<readonly string[] | null>(null)
  /** 钻入页报上来的面包屑（null = 普通页头） */
  const [crumb, setCrumb] = useState<SettingsCrumb | null>(null)
  /**
   * 哪一页挂着没存的草稿（样式 / 规范页报上来；同一时刻只挂着一页）。切分区、关设置
   * 会把那一页卸掉、草稿随之丢掉，所以先问一句；导航项上挂一个点（设计审计 2026-10-07 §9.1）。
   */
  const [dirtySection, setDirtySection] = useState<SectionId | null>(null)
  const reportDirty = (id: SectionId) => (dirty: boolean) =>
    setDirtySection((cur) => (dirty ? id : cur === id ? null : cur))

  // effect 里要读「此刻」的分区与草稿页，不吃闭包里的旧值
  const sectionRef = useRef(section)
  sectionRef.current = section
  const dirtyRef = useRef(dirtySection)
  dirtyRef.current = dirtySection

  // 调用方指定分区时（如顶栏「有新版本」、桌面菜单「检查更新 / 诊断」）跳过去，之后仍由用户自由切换。
  // 设置已开着、当前页挂着没存的草稿时，外部请求同样先问（Codex #821 P1）——否则直接换页会卸掉那一页、静默丢草稿
  useEffect(() => {
    if (!open) return
    const target = resolveSection(requested)
    if (!target) return
    // 已在目标页：无事可做，但请求同样要消费，否则下次同一分区的菜单命令 store 值不变、被吞（Codex #821 P2）
    if (target === sectionRef.current) {
      if (useUiStore.getState().settingsSection === requested) useUiStore.setState({ settingsSection: null })
      return
    }
    let cancelled = false
    void (async () => {
      const cur = sectionRef.current
      const go = dirtyRef.current !== cur || (await askDiscardDraft())
      if (go && !cancelled && sectionRef.current === cur) setSection(target)
      // 请求一律消费掉：否则「继续编辑」之后再从菜单发同一个分区，store 值不变、effect 不重跑，命令被吞（Codex #821 P2）
      if (!cancelled && useUiStore.getState().settingsSection === requested) {
        useUiStore.setState({ settingsSection: null })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, requested])

  // 关掉设置时清掉搜索词：下次打开是一份完整的导航，不是上一次没搜完的半截
  useEffect(() => {
    if (!open) setQuery('')
  }, [open])

  // 切页：内容区滚回顶部。焦点留在导航——用户正在导航
  useLayoutEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0
  }, [section])

  // 搜索结果落地：那一页挂上之后把那一行滚进视野、亮一下
  useEffect(() => {
    if (!pendingAnchor) return
    const el = pendingAnchor
      .map((id) => contentRef.current?.querySelector<HTMLElement>(`[data-settings-anchor="${id}"]`))
      .find(Boolean)
    setPendingAnchor(null)
    if (!el) return
    el.scrollIntoView?.({ block: 'center' })
    el.setAttribute('data-settings-hit', '')
    // 计时器不挂在这个 effect 的 cleanup 上：上面的 setPendingAnchor(null) 会让它马上重跑，
    // cleanup 一跑就把刚起的计时器撤了，高亮便永远留在那一行（Codex #828 P2）
    hitTimers.current.add(
      window.setTimeout(() => el.removeAttribute('data-settings-hit'), SEARCH_HIT_MS),
    )
  }, [pendingAnchor, section])
  useEffect(() => {
    const timers = hitTimers.current
    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [])

  const label = (id: SectionId) => st(`section.${id}`)
  // 命中与否按当前语言的标签 / 关键词算：换了界面语言要重算，不能只看搜索词（Codex #828 P2）
  const results = useMemo(() => searchSettings(query, SECTIONS, label), [query, i18n.language]) // eslint-disable-line react-hooks/exhaustive-deps
  // 搜索框只在 ≥640px 出现：窄下去时不再按搜索词过滤，否则用户看不见也清不掉它，
  // 无匹配时导航整个空掉（Codex #828 P2）。搜索词留着，宽回来原样接着搜
  const searching = searchShown && query.trim() !== ''
  /** 导航里此刻摆着的分区（搜索时只剩命中的）：方向键只在它们之间走 */
  const visible: SectionId[] = searching ? results.map((r) => r.section) : SECTIONS

  if (!open) return null
  /** 当前页有没存的草稿时先问；「继续编辑」= false，什么都不动 */
  const leaveSection = async () => dirtySection !== section || (await askDiscardDraft())
  const close = async () => {
    if (!(await leaveSection())) return
    setOpen(false)
  }
  const go = async (id: SectionId, anchor?: readonly string[]) => {
    if (id !== section) {
      if (!(await leaveSection())) return
      setSection(id)
    } else if (anchor && crumb) {
      // 同一分区里正挂着钻入页（改图助手 › Codex）：锚点都在列表态上，先退回列表再落地（Codex #828 P2）
      crumb.onBack()
    }
    if (anchor) setPendingAnchor(anchor)
    else navRef.current?.querySelector<HTMLButtonElement>(`[data-section="${id}"]`)?.focus()
  }

  const onNavKey = (e: KeyboardEvent<HTMLElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End']
    if (!keys.includes(e.key)) return
    // 只管分区项之间的走动：搜索结果的行项有自己的 Tab 顺序（搜索框自己吞掉按键，不冒泡到这里）
    if ((e.target as HTMLElement).closest('[data-settings-result]')) return
    e.preventDefault()
    if (!visible.length) return
    const i = Math.max(0, visible.indexOf(section))
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? visible.length - 1
          : e.key === 'ArrowDown' || e.key === 'ArrowRight'
            ? (i + 1) % visible.length
            : (i - 1 + visible.length) % visible.length
    void go(visible[next])
  }

  const navItem = (id: SectionId) => (
    <button
      key={id}
      type="button"
      data-section={id}
      onClick={() => void go(id)}
      aria-current={section === id || undefined}
      // roving tabindex：Tab 只落在当前项，方向键在项之间走（搜索时当前页可能被滤掉——那时第一项接手）
      tabIndex={section === id || (!visible.includes(section) && visible[0] === id) ? 0 : -1}
      className={cn(
        // 选中 = 轻 tint + 600（宪法第二十六节：选中靠字重再说一遍），不靠大块深灰——与列表 / 树行同一份
        // `listRowClass`；导航项 30px、13px、不缩进（行在 nav 的内边距里）
        listRowClass({ size: 'sm', selected: section === id, muted: section !== id }),
        'mx-0 h-7.5 shrink-0 whitespace-nowrap px-2.5 text-left text-base',
      )}
    >
      {label(id)}
      {/* 这一页有没存的改动：6px 的点，读屏读名字后面那句（data-nav-dirty 给用例认） */}
      {dirtySection === id && (
        <span data-nav-dirty className="ml-1.5 inline-flex">
          <span aria-hidden className="size-1.5 rounded-full bg-accent" />
          <span className="sr-only">{translate('draftGuard.unsaved', { ns: 'dialogs' })}</span>
        </span>
      )}
    </button>
  )

  const normal = CONTENT_MODE[section] === 'normal'

  return (
    <Dialog
      open
      onOpenChange={(v) => (v ? setOpen(true) : void close())}
      title={st('title')}
      width={SHELL_WIDTH}
      height={SHELL_HEIGHT}
      covered={covered}
      chrome="shell"
    >
      <div data-settings-shell className="flex h-full min-h-0 flex-col sm:flex-row">
        <nav
          ref={navRef}
          aria-label={st('navLabel')}
          onKeyDown={onNavKey}
          className={cn(
            // 窄窗口：一行可横滚的分区条（组名与搜索藏起来、组之间只留一点间距）；
            // ≥640px：左侧固定 200px 一列，按组分层，与内容区之间一根 hairline
            'flex shrink-0 gap-3 overflow-x-auto px-2 py-2',
            'sm:w-50 sm:flex-col sm:gap-4 sm:overflow-x-visible sm:overflow-y-auto sm:px-3 sm:py-3',
            'border-b border-border sm:border-b-0 sm:border-r',
          )}
        >
          <div className="hidden shrink-0 sm:block">
            <SearchInput
              type="search"
              data-settings-search
              value={query}
              onValueChange={setQuery}
              placeholder={st('search.placeholder')}
              aria-label={st('search.label')}
              clearLabel={st('search.clear')}
              onKeyDown={(e) => {
                // ↓ 从搜索框进到第一项（结果或分区）
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  navRef.current?.querySelector<HTMLButtonElement>('[data-section], [data-settings-result]')?.focus()
                }
              }}
            />
          </div>
          {searching ? (
            <div data-settings-results className="flex shrink-0 gap-0.5 sm:flex-col">
              {results.length === 0 && (
                <p data-settings-no-results className="type-caption hidden px-2.5 sm:block">
                  {st('search.noResults')}
                </p>
              )}
              {results.map((r) => (
                <div key={r.section} className="flex shrink-0 gap-0.5 sm:flex-col">
                  {navItem(r.section)}
                  {r.entries.map((e) => (
                    <button
                      key={e.id}
                      type="button"
                      data-settings-result={e.id}
                      onClick={() => void go(r.section, anchorsOf(e))}
                      className={cn(
                        'hidden h-7 min-w-0 shrink-0 items-center truncate rounded-md pl-5 pr-2 text-left text-sm text-ink-2 outline-none sm:flex',
                        'hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
                      )}
                    >
                      <span className="truncate">{e.label()}</span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            NAV_GROUPS.map((g) => (
              <div key={g.id} data-nav-group={g.id} className="flex shrink-0 gap-0.5 sm:flex-col">
                {/* 组名 12 / 500 / ink-3：比项淡一档（颜色），字重留在 500——选中项已经是 600，
                    组名不会与它读成同一层（2026-10-07 设计审计 §9.1） */}
                <span className="hidden px-2.5 pb-1 text-sm font-medium text-ink-3 sm:block">
                  {st(`navGroup.${g.id}`)}
                </span>
                {g.sections.map(navItem)}
              </div>
            ))
          )}
        </nav>
        <div
          ref={contentRef}
          data-settings-content
          // 常驻滚动轨道：Windows WebKit 的自定义滚动条不会靠 scrollbar-gutter 预留空间；
          // auto 等回包撑高后才占 6px，会把居中的内容列左挪 3px，移动正在按的入口。
          // 底部留 mb 让滚动条在圆角之前就结束，不会贴着圆角被削
          className="mb-2 min-h-0 min-w-0 flex-1 overflow-y-scroll overflow-x-hidden px-6 py-5 [scrollbar-gutter:stable]"
        >
          <SettingsCrumbContext.Provider value={setCrumb}>
            <div
              // key：换页时整列重挂，进场淡入一次（cross-fade 的「进」那一半；退场不保活，切页不拖泥带水）
              key={section}
              data-content-mode={CONTENT_MODE[section]}
              style={normal ? { maxWidth: CONTENT_MAX_WIDTH } : undefined}
              className={cn('flex flex-col gap-6 motion-safe:animate-fade-in', normal && 'mx-auto w-full')}
            >
              <header data-settings-page-header className="flex min-w-0 flex-col gap-0.5">
                {crumb ? (
                  <h2 className="type-heading flex min-w-0 items-center gap-1.5">
                    <button
                      type="button"
                      {...(crumb.backAnchor ? { [crumb.backAnchor]: '' } : {})}
                      aria-label={crumb.backLabel}
                      onClick={crumb.onBack}
                      className="shrink-0 rounded-sm text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
                    >
                      {label(section)}
                    </button>
                    <ChevronRight size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-3" />
                    <span className="min-w-0 truncate">{crumb.current}</span>
                  </h2>
                ) : (
                  <>
                    <h2 className="type-heading">{label(section)}</h2>
                    <p className="type-caption">{PAGE_DESC[section]()}</p>
                  </>
                )}
              </header>
              {section === 'general' && <GeneralSettings close={() => void close()} />}
              {section === 'project' && <ProjectSettings />}
              {section === 'style' && <ProfilesSettings kind="style" onDirtyChange={reportDirty('style')} />}
              {section === 'spec' && <ProfilesSettings kind="spec" onDirtyChange={reportDirty('spec')} />}
              {section === 'export' && <ExportSettings />}
              {section === 'ai' && <CodingAgentsSection />}
              {section === 'packages' && <PackagesSettings />}
              {section === 'diagnostics' && <DiagnosticsSettings />}
              {section === 'about' && <PrivacyAboutSettings />}
            </div>
          </SettingsCrumbContext.Provider>
        </div>
      </div>
    </Dialog>
  )
}
