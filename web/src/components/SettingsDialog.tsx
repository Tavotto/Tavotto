import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'
import { CodingAgentsSection } from './settings/CodingAgentsSection'
import { DiagnosticsSettings } from './settings/DiagnosticsSettings'
import { ExportSettings } from './settings/ExportSettings'
import { GeneralSettings } from './settings/GeneralSettings'
import { InterfaceSettings } from './settings/InterfaceSettings'
import { PackagesSettings } from './settings/PackagesSettings'
import { PrivacyAboutSettings } from './settings/PrivacyAboutSettings'
import { ProfilesSettings } from './settings/ProfilesSettings'
import { ProjectSettings } from './settings/ProjectSettings'
import { UpdateSettings } from './settings/UpdateSettings'

/**
 * 设置对话框的**外壳**：导航 + 分区分派，仅此而已（ADR 0038）。
 *
 * 外壳合同（`UX_CONTRACTS.md` 5d）：
 *   * **尺寸固定**：宽 `SHELL_WIDTH`、高 `SHELL_HEIGHT`（上限 86vh / 视口宽减
 *     2rem），切分区时外框一个像素都不动；内容区自己滚，标题与导航固定；
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
  | 'interface'
  | 'project'
  | 'style'
  | 'spec'
  | 'export'
  // 分区 **id** 仍是 'ai'（AiPanel 的「打开设置」按它跳转，改名等于断掉那条路径）
  | 'ai'
  | 'packages'
  | 'diagnostics'
  | 'update'
  | 'about'

export const SECTIONS: SectionId[] = [
  'general',
  'interface',
  'project',
  'style',
  'spec',
  'export',
  'ai',
  'packages',
  'diagnostics',
  'update',
  'about',
]

/**
 * 旧分区 id → 新分区。深链的调用方（导出面板 / 素材库 / AiPanel）与用户的
 * 肌肉记忆都可能还带着旧名字；不认识的一律回到「常规」而不是白屏。
 */
const ALIASES: Record<string, SectionId> = {
  profiles: 'spec',
  canvas: 'interface',
  sidebars: 'interface',
  shortcuts: 'general',
}

export function resolveSection(requested: string | null | undefined): SectionId | null {
  if (!requested) return null
  if ((SECTIONS as string[]).includes(requested)) return requested as SectionId
  return ALIASES[requested] ?? null
}

/** 外壳尺寸（一个出处；e2e 与 vitest 按它量） */
export const SHELL_WIDTH = 760
/** 固定高；Dialog 自带 `max-h-[86vh]`，小屏上由它收缩 */
export const SHELL_HEIGHT = '600px'

/** 本对话框的文案在 dialogs:settings.* 下 */
const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function SettingsDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.settingsOpen)
  const setOpen = useUiStore((s) => s.setSettingsOpen)
  const requested = useUiStore((s) => s.settingsSection)
  const [section, setSection] = useState<SectionId>('general')
  const navRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)

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
  const close = () => setOpen(false)

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
    setSection(SECTIONS[next])
    navRef.current
      ?.querySelector<HTMLButtonElement>(`[data-section="${SECTIONS[next]}"]`)
      ?.focus()
  }

  return (
    <Dialog
      open
      onOpenChange={setOpen}
      title={st('title')}
      width={SHELL_WIDTH}
      height={SHELL_HEIGHT}
    >
      <div data-settings-shell className="flex h-full min-h-0 flex-col gap-2 sm:flex-row sm:gap-3">
        <nav
          ref={navRef}
          aria-label={st('navLabel')}
          onKeyDown={onNavKey}
          className={cn(
            // 窄窗口：一行可横滚的分区条；≥640px：左侧固定一列
            'flex shrink-0 gap-0.5 overflow-x-auto sm:w-36 sm:flex-col sm:overflow-x-visible',
            'border-b border-border pb-2 sm:border-b-0 sm:border-r sm:pb-0 sm:pr-2',
          )}
        >
          {SECTIONS.map((id) => (
            <button
              key={id}
              type="button"
              data-section={id}
              onClick={() => setSection(id)}
              aria-current={section === id || undefined}
              // roving tabindex：Tab 只落在当前项，方向键在项之间走
              tabIndex={section === id ? 0 : -1}
              className={cn(
                'relative h-7 shrink-0 whitespace-nowrap rounded-sm px-2 text-left text-xs outline-none focus-visible:focus-ring',
                section === id
                  ? 'bg-accent-subtle font-medium text-ink'
                  : 'text-ink-2 hover:bg-ink/[.045]',
              )}
            >
              {section === id && (
                <span
                  aria-hidden
                  className="absolute left-0 top-1.5 hidden h-4 w-0.5 rounded-full bg-accent sm:block"
                />
              )}
              {st(`section.${id}`)}
            </button>
          ))}
        </nav>
        <div
          ref={contentRef}
          data-settings-content
          className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden pr-1"
        >
          <h2 className="sr-only">{st(`section.${section}`)}</h2>
          {section === 'general' && <GeneralSettings close={close} />}
          {section === 'interface' && <InterfaceSettings close={close} />}
          {section === 'project' && <ProjectSettings />}
          {section === 'style' && <ProfilesSettings kind="style" />}
          {section === 'spec' && <ProfilesSettings kind="spec" />}
          {section === 'export' && <ExportSettings />}
          {section === 'ai' && <CodingAgentsSection />}
          {section === 'packages' && <PackagesSettings />}
          {section === 'diagnostics' && <DiagnosticsSettings />}
          {section === 'update' && <UpdateSettings />}
          {section === 'about' && <PrivacyAboutSettings />}
        </div>
      </div>
    </Dialog>
  )
}
