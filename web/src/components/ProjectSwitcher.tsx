import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Folder, SquareArrowOutUpRight } from 'lucide-react'
import { literal } from '@/i18n'
import { cn } from '@/lib/utils'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { DirBrowser } from './ProjectPicker'
import { Menu, MenuItem, MenuLabel, MenuSeparator } from './ui/Menu'
import { Tip } from './ui/Tooltip'

/** 最近项目在菜单里最多列这些条，再多就去 Picker 看完整列表 */
const RECENT_IN_MENU = 6

/**
 * 顶栏左上角的项目切换器。
 *
 * 切图库以前只有一条路：设置 → 项目与路径 → 切换项目 → Picker，四步。用户
 * 手里同时开着「正文图」和「补充材料」两个图库时，这四步一天要走十几趟。
 * 这里把它压成一次点击，并额外给出「在新标签页打开」——项目是绑在标签页
 * 上的（lib/session.ts），所以两个图库可以真正同时开着，互不干扰。
 */
export function ProjectSwitcher() {
  const { t } = useTranslation('project')
  const project = useProjectStore((s) => s.project)
  const recent = useProjectStore((s) => s.recent)
  const opened = useProjectStore((s) => s.opened)
  const open = useProjectStore((s) => s.open)
  const [browse, setBrowse] = useState<null | 'open' | 'create'>(null)

  if (!project?.open) return null

  const go = (path: string, create = false) => {
    void open(path, create).catch((e: unknown) =>
      // 后端的原始报错原样透出（诊断信息），不当作界面文案翻译
      useUiStore.getState().setStatus(literal(e instanceof Error ? e.message : String(e)), 'error'),
    )
  }

  // 「在新标签页打开」直接开一个带 pj 的地址：新标签页自己的 sessionStorage
  // 会认下这个项目，与本标签页各走各的
  const openInNewTab = (id?: string | null) => {
    const url = id ? `${location.pathname}?pj=${encodeURIComponent(id)}` : location.pathname
    window.open(url, '_blank', 'noopener')
  }

  const others = opened.filter((p) => p.id !== project.id)
  const recentRest = recent
    .filter((r) => r.path !== project.figures_dir && !opened.some((o) => o.figures_dir === r.path))
    .slice(0, RECENT_IN_MENU)

  return (
    <>
      <Menu
        width={280}
        trigger={
          <button
            className={cn(
              'flex h-7 min-w-0 max-w-56 shrink items-center gap-1 rounded-md px-1.5 text-xs',
              'text-ink-2 outline-none hover:bg-ink/[.045] hover:text-ink focus-visible:focus-ring',
            )}
            aria-label={t('switcher.trigger', { name: project.name })}
          >
            <Folder size={13} className="shrink-0 text-ink-3" />
            <span className="truncate">{project.name}</span>
            <ChevronDown size={12} className="shrink-0 text-ink-3" />
          </button>
        }
      >
        <MenuLabel>{t('switcher.current')}</MenuLabel>
        {/* 宽度必须钉死：图库路径动辄上百字符，不封顶会把整个浮层撑成一条 */}
        <div className="w-[264px] px-2 pb-1">
          <div className="truncate font-mono text-xs text-ink-2" title={project.figures_dir}>
            {project.figures_dir}
          </div>
          <div className="mt-0.5 text-xs text-ink-3">
            {t('switcher.scriptCount', { count: project.scripts ?? 0 })}
            {project.settings?.allow_write_back === false && t('switcher.readOnlySuffix')}
          </div>
        </div>

        {others.length > 0 && (
          <>
            <MenuSeparator />
            <MenuLabel>{t('switcher.opened')}</MenuLabel>
            {others.map((p) => (
              <MenuItem key={p.id} onSelect={() => go(p.figures_dir!)}>
                {p.name}
              </MenuItem>
            ))}
          </>
        )}

        {recentRest.length > 0 && (
          <>
            <MenuSeparator />
            <MenuLabel>{t('switcher.recent')}</MenuLabel>
            {recentRest.map((r) => (
              <MenuItem key={r.path} disabled={!r.exists} onSelect={() => go(r.path)}>
                {r.name}
              </MenuItem>
            ))}
          </>
        )}

        <MenuSeparator />
        <MenuItem onSelect={() => useUiStore.getState().setRegistryOpen(true)}>
          {t('switcher.registry')}
        </MenuItem>
        <MenuItem onSelect={() => setBrowse('open')}>{t('switcher.browse')}</MenuItem>
        <MenuItem onSelect={() => setBrowse('create')}>{t('switcher.create')}</MenuItem>
        <MenuItem onSelect={() => openInNewTab(project.id)}>
          {t('switcher.openInNewTab')}
        </MenuItem>
        <MenuItem onSelect={() => useProjectStore.setState({ phase: 'none' })}>
          {t('switcher.allProjects')}
        </MenuItem>
      </Menu>

      {browse && (
        <DirBrowser
          mode={browse}
          initialPath={project.figures_dir}
          onClose={() => setBrowse(null)}
          onPick={(path, create) => {
            setBrowse(null)
            go(path, create)
          }}
        />
      )}
    </>
  )
}

/** 顶栏上「把当前项目再开一个标签页」的快捷入口（图标按钮，不占字宽） */
export function OpenInNewTabButton() {
  const { t } = useTranslation('project')
  const id = useProjectStore((s) => s.project?.id)
  if (!id) return null
  return (
    <Tip label={t('switcher.newTabTip')}>
      <button
        onClick={() =>
          window.open(`${location.pathname}?pj=${encodeURIComponent(id)}`, '_blank', 'noopener')
        }
        aria-label={t('switcher.newTabLabel')}
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-3',
          'outline-none hover:bg-ink/[.045] hover:text-ink focus-visible:focus-ring',
        )}
      >
        <SquareArrowOutUpRight size={13} />
      </button>
    </Tip>
  )
}
