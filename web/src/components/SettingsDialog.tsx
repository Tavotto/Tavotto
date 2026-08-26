import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from './ui/Dialog'
import { CodingAgentsSection } from './settings/CodingAgentsSection'
import { CanvasSettings } from './settings/CanvasSettings'
import { ExportSettings } from './settings/ExportSettings'
import { GeneralSettings } from './settings/GeneralSettings'
import { PrivacyAboutSettings } from './settings/PrivacyAboutSettings'
import { ProjectSettings } from './settings/ProjectSettings'
import { ShortcutSettings } from './settings/ShortcutSettings'
import { SidebarSettings } from './settings/SidebarSettings'
import { UpdateSettings } from './settings/UpdateSettings'

/**
 * 设置对话框的**外壳**：导航 + 分区分派，仅此而已。
 *
 * 每个分区各住一个文件（`components/settings/`）。修改前这里是 783 行，
 * 九个分区的表单、遥测说明、About 与诊断全挤在一个文件里，任何一处改动都要
 * 在同一个文件里翻半天。行与帮助的基础构件在 `settings/SettingRow.tsx`。
 */

type SectionId =
  | 'general'
  | 'project'
  | 'canvas'
  | 'sidebars'
  // 分区 **id** 仍是 'ai'（AiPanel 的「打开设置」按它跳转，改名等于断掉那条
  // 路径）；显示名换成了「编码 Agent / Coding Agents」，在 dialogs.json 里。
  | 'ai'
  | 'export'
  | 'shortcuts'
  | 'update'
  | 'about'

/** 本对话框的文案在 dialogs:settings.* 下 */
const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const SECTIONS: SectionId[] = [
  'general',
  'project',
  'canvas',
  'sidebars',
  'ai',
  'export',
  'shortcuts',
  'update',
  'about',
]

export function SettingsDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.settingsOpen)
  const setOpen = useUiStore((s) => s.setSettingsOpen)
  const requested = useUiStore((s) => s.settingsSection)
  const [section, setSection] = useState<SectionId>('general')
  // 调用方指定分区时（如顶栏「有新版本」）跳过去，之后仍由用户自由切换
  useEffect(() => {
    if (open && requested) setSection(requested as SectionId)
  }, [open, requested])

  if (!open) return null
  const close = () => setOpen(false)
  return (
    <Dialog open onOpenChange={setOpen} title={st('title')} size="lg">
      <div className="flex min-h-72 gap-3">
        <nav aria-label={st('navLabel')} className="flex w-32 shrink-0 flex-col gap-0.5">
          {SECTIONS.map((id) => (
            <button
              key={id}
              onClick={() => setSection(id)}
              aria-current={section === id || undefined}
              className={cn(
                'relative h-7 rounded-sm px-2 text-left text-xs outline-none focus-visible:focus-ring',
                section === id
                  ? 'bg-accent-subtle font-medium text-ink'
                  : 'text-ink-2 hover:bg-ink/[.045]',
              )}
            >
              {section === id && (
                <span
                  aria-hidden
                  className="absolute left-0 top-1.5 h-4 w-0.5 rounded-full bg-accent"
                />
              )}
              {st(`section.${id}`)}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto pr-1">
          {section === 'general' && <GeneralSettings />}
          {section === 'project' && <ProjectSettings />}
          {section === 'canvas' && <CanvasSettings close={close} />}
          {section === 'sidebars' && <SidebarSettings />}
          {section === 'ai' && <CodingAgentsSection />}
          {section === 'export' && <ExportSettings />}
          {section === 'shortcuts' && <ShortcutSettings close={close} />}
          {section === 'update' && <UpdateSettings />}
          {section === 'about' && <PrivacyAboutSettings />}
        </div>
      </div>
    </Dialog>
  )
}
