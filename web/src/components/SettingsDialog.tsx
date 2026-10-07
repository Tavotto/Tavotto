import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { dialogCovered, useUiStore } from '@/store/uiStore'
import { askDiscardDraft } from './askDiscardDraft'
import { Dialog } from './ui/Dialog'
import { CodingAgentsSection } from './settings/CodingAgentsSection'
import { DiagnosticsSettings } from './settings/DiagnosticsSettings'
import { ExportSettings } from './settings/ExportSettings'
import { GeneralSettings } from './settings/GeneralSettings'
import { PackagesSettings } from './settings/PackagesSettings'
import { PrivacyAboutSettings } from './settings/PrivacyAboutSettings'
import { ProfilesSettings } from './settings/ProfilesSettings'
import { ProjectSettings } from './settings/ProjectSettings'

/**
 * 设置对话框的**外壳**：导航 + 分区分派，仅此而已（ADR 0038）。
 *
 * 外壳合同（`UX_CONTRACTS.md` 5d；形态见 Design Constitution 第十二节）：
 *   * **尺寸固定**：宽 `SHELL_WIDTH`、高 `SHELL_HEIGHT`（上限 86vh / 视口宽减
 *     2rem，小窗口按它收缩），切分区时外框一个像素都不动；内容区自己滚，标题与
 *     导航固定；
 *   * **内容有最大宽度**：普通分区的有效内容宽 `CONTENT_MAX_WIDTH`，不铺满整个
 *     窗口；要放预览 / 清单 / 表格的分区声明 `wide`（`CONTENT_MODE`），不由每一页
 *     自己决定；
 *   * **导航分组**：九个分区按 `NAV_GROUPS` 分四组（通用 / 工作流 / 集成 / 系统），
 *     信息架构与顺序不变，只是视觉上用间距与一行极低权重的组名分层；
 *   * **小窗口 / 大缩放**：<640px 时导航从左栏变成顶部一行可横滚的分区条，
 *     内容区仍独立滚动，绝不横向溢出；
 *   * **切页策略**：内容区滚回顶部、焦点留在导航（用户在导航），↑ ↓ Home End
 *     在导航里走、Enter / Space 选中；
 *   * desktop / browser 复用同一个外壳——这里没有任何平台分支。
 *
 * 每个分区各住一个文件（`components/settings/`）。行与帮助的基础构件在
 * `settings/SettingRow.tsx`。
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
/** 普通分区的有效内容宽（px）：一行设置 = 标题列 + 240 的控件列，再宽就读不成一列 */
export const CONTENT_MAX_WIDTH = 640

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
 * 每个分区的内容宽度模式。`normal`：内容居左、最大 `CONTENT_MAX_WIDTH`；`wide`：
 * 铺满内容区（左清单 + 右编辑器 / 预览 / 表格那种）。不给每一页自己随意布局。
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

export function SettingsDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.settingsOpen)
  const setOpen = useUiStore((s) => s.setSettingsOpen)
  const requested = useUiStore((s) => s.settingsSection)
  // 上面还压着论文样式对话框时整层藏起来（状态不丢），它关掉就回来
  const covered = useUiStore((s) => dialogCovered(s.dialogStack, 'settings'))
  const [section, setSection] = useState<SectionId>('general')
  const navRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  /**
   * 哪一页挂着没存的草稿（样式 / 规范页报上来；同一时刻只挂着一页）。切分区、关设置
   * 会把那一页卸掉、草稿随之丢掉，所以先问一句；导航项上挂一个点（设计审计 2026-10-07 §9.1）。
   */
  const [dirtySection, setDirtySection] = useState<SectionId | null>(null)
  const reportDirty = (id: SectionId) => (dirty: boolean) =>
    setDirtySection((cur) => (dirty ? id : cur === id ? null : cur))

  // 调用方指定分区时（如顶栏「有新版本」）跳过去，之后仍由用户自由切换
  useEffect(() => {
    if (!open) return
    const target = resolveSection(requested)
    if (target) setSection(target)
  }, [open, requested])

  // 切页：内容区滚回顶部。焦点留在导航——用户正在导航
  useLayoutEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0
  }, [section])

  if (!open) return null
  /** 当前页有没存的草稿时先问；「继续编辑」= false，什么都不动 */
  const leaveSection = async () => dirtySection !== section || (await askDiscardDraft())
  const close = async () => {
    if (!(await leaveSection())) return
    setOpen(false)
  }
  const go = async (id: SectionId) => {
    if (id === section) return
    if (!(await leaveSection())) return
    setSection(id)
    navRef.current?.querySelector<HTMLButtonElement>(`[data-section="${id}"]`)?.focus()
  }

  const onNavKey = (e: KeyboardEvent<HTMLElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End']
    if (!keys.includes(e.key)) return
    e.preventDefault()
    const i = SECTIONS.indexOf(section)
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? SECTIONS.length - 1
          : e.key === 'ArrowDown' || e.key === 'ArrowRight'
            ? (i + 1) % SECTIONS.length
            : (i - 1 + SECTIONS.length) % SECTIONS.length
    void go(SECTIONS[next])
  }

  const navItem = (id: SectionId) => (
    <button
      key={id}
      type="button"
      data-section={id}
      onClick={() => void go(id)}
      aria-current={section === id || undefined}
      // roving tabindex：Tab 只落在当前项，方向键在项之间走
      tabIndex={section === id ? 0 : -1}
      className={cn(
        'relative h-7 shrink-0 whitespace-nowrap rounded-sm px-2 text-left text-sm outline-none focus-visible:focus-ring',
        // 选中 = 轻 tint + 字重（Design Constitution 第五节），不靠大块深灰
        section === id ? 'bg-selected font-medium text-ink' : 'text-ink-2 hover:bg-surface-hover',
      )}
    >
      {st(`section.${id}`)}
      {/* 这一页有没存的改动：6px 的点，读屏读名字后面那句（data-nav-dirty 给用例认） */}
      {dirtySection === id && (
        <span data-nav-dirty className="ml-1.5 inline-flex align-middle">
          <span aria-hidden className="size-1.5 rounded-full bg-accent" />
          <span className="sr-only">{translate('draftGuard.unsaved', { ns: 'dialogs' })}</span>
        </span>
      )}
    </button>
  )

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
            // 窄窗口：一行可横滚的分区条（组名藏起来、组之间只留一点间距）；
            // ≥640px：左侧固定一列，按组分层，与内容区之间一根 hairline
            'flex shrink-0 gap-3 overflow-x-auto px-2 py-2',
            'sm:w-48 sm:flex-col sm:gap-4 sm:overflow-x-visible sm:overflow-y-auto sm:px-3 sm:py-3',
            'border-b border-border sm:border-b-0 sm:border-r',
          )}
        >
          {NAV_GROUPS.map((g) => (
            <div key={g.id} data-nav-group={g.id} className="flex shrink-0 gap-0.5 sm:flex-col">
              {/* 组名比项淡一档、不加重：此前组名（type-section 12/500/ink）与选中项同色同重，
                  层级只剩 1px 字号差，读起来是两层同权的标题（全面打磨 D07，用户拍板） */}
              <span className="hidden px-2 pb-1 text-sm text-ink-3 sm:block">
                {st(`navGroup.${g.id}`)}
              </span>
              {g.sections.map(navItem)}
            </div>
          ))}
        </nav>
        <div
          ref={contentRef}
          data-settings-content
          // scrollbar-gutter 常驻：有没有滚动条内容都从同一条竖线起排，切页不左右跳；
          // 底部留 mb 让滚动条在圆角之前就结束，不会贴着 12px 圆角被削
          className="mb-2 min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-5 [scrollbar-gutter:stable]"
        >
          <h2 className="sr-only">{st(`section.${section}`)}</h2>
          <div
            data-content-mode={CONTENT_MODE[section]}
            style={CONTENT_MODE[section] === 'normal' ? { maxWidth: CONTENT_MAX_WIDTH } : undefined}
            className="flex flex-col gap-7"
          >
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
        </div>
      </div>
    </Dialog>
  )
}
