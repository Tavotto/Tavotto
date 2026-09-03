import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { create } from 'zustand'
import { msg, t as translate } from '@/i18n'
import { Search } from 'lucide-react'
import { cn, MOD } from '@/lib/utils'
import {
  addSubLabels,
  addText,
  createLayoutGroup,
  enterElementEdit,
  groupSelected,
  newBlankDocument,
  runManualSave,
  selectAll,
  ungroupSelected,
} from '@/store/actions'
import { canCycleOverlapSelection, cycleOverlapSelection } from '@/canvas/interactions'
import { resetHints, resetTutorial, runTutorialEntry, tutorialEntry } from '@/lib/onboarding/tutorial'
import { useDocumentStore } from '@/store/documentStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'

/**
 * ⌘K 命令面板：把散在菜单里的动作变成一个可搜索入口。
 * 命令列表是精选的高频动作，不追求全量——低频动作留在原来的菜单里。
 */

interface PaletteState {
  open: boolean
  setOpen: (v: boolean) => void
}

export const usePalette = create<PaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}))

/**
 * 命令的**文案不在这里**：label 与 keywords 都按 id 查
 * `dialogs:palette.commands.<id>.{label,keywords}`。keywords 是每种语言各自
 * 的搜索词（中文那份带拼音首字母，英文那份带同义词），所以它必须跟着语言
 * 走，不能只翻 label。
 */
interface Command {
  id: string
  shortcut?: string
  /** 需要选中对象才可用 */
  needsSelection?: boolean
  /** 按状态决定出不出现（教程的开始 / 继续 / 重置互斥）；不给 = 一直在 */
  available?: () => boolean
  run: () => void
}

const ui = () => useUiStore.getState()
/** 项目相关的命令只在项目打开着时出现（embedded / playground 里没有项目，整组不出现） */
const projectOpen = () => useProjectStore.getState().phase === 'open'

/**
 * 命令 id 是**稳定标识**（文案与关键词按 id 查资源；e2e 与遥测都认它），
 * 改名等于换一条命令。动作全部复用真实 action / helper：刷新走
 * `liveSync.refreshProjectNow`（统一刷新端点），接入状态走
 * `projectReadinessStore.openCenter`，教程三条走 `lib/onboarding/tutorial`——
 * 顶栏「更多」与设置页调的是同一批函数，这里不判状态、不另写一份。
 */
const COMMANDS: Command[] = [
  // 项目：刷新（检查新文件）与接入状态
  {
    id: 'refresh-project',
    available: projectOpen,
    run: () => void refreshProjectNow(),
  },
  {
    id: 'readiness',
    available: projectOpen,
    run: () => {
      // 当前选中的是一张图就直接聚焦到它那一行
      const id = useSelectionStore.getState().primary()
      const o = id ? useDocumentStore.getState().doc.objects.find((x) => x.id === id) : null
      const focus = o?.type === 'panel' ? o.fileId : null
      useProjectReadinessStore.getState().openCenter({ focus, source: 'palette' })
    },
  },
  // 教程三条：状态判据只有 lib/onboarding/tutorial 一份，这里只挑显示哪条
  {
    id: 'tutorial-start',
    available: () => tutorialEntry() !== 'resume',
    run: () => void runTutorialEntry('palette'),
  },
  {
    id: 'tutorial-resume',
    available: () => tutorialEntry() === 'resume',
    run: () => void runTutorialEntry('palette'),
  },
  {
    id: 'tutorial-reset',
    available: () => useOnboardingStore.getState().tutorialProjectId != null,
    run: () => void resetTutorial(),
  },
  { id: 'hints-reset', run: () => resetHints() },
  { id: 'export', shortcut: `${MOD}E`, run: () => ui().setExportOpen(true) },
  { id: 'save-document', shortcut: `${MOD}S`, run: () => void runManualSave() },
  { id: 'save-layout', shortcut: `⇧${MOD}S`, run: () => ui().setLayoutOpen(true, 'save') },
  { id: 'load-layout', run: () => ui().setLayoutOpen(true, 'load') },
  { id: 'versions', run: () => ui().setVersionsOpen(true) },
  { id: 'styles', run: () => ui().setStylesOpen(true) },
  { id: 'new-doc', run: () => void newBlankDocument() },
  { id: 'add-text', shortcut: 'T', run: () => void addText() },
  { id: 'sub-labels', run: addSubLabels },
  { id: 'select-all', shortcut: `${MOD}A`, run: selectAll },
  { id: 'group', needsSelection: true, run: groupSelected },
  { id: 'ungroup', needsSelection: true, run: ungroupSelected },
  { id: 'layout-row', needsSelection: true, run: () => createLayoutGroup('row') },
  { id: 'layout-col', needsSelection: true, run: () => createLayoutGroup('col') },
  { id: 'layout-grid', needsSelection: true, run: () => createLayoutGroup('grid') },
  {
    id: 'edit-elements',
    needsSelection: true,
    run: () => {
      const id = useSelectionStore.getState().primary()
      const o = id ? useDocumentStore.getState().doc.objects.find((x) => x.id === id) : null
      if (o?.type === 'panel' && o.script) enterElementEdit(o.id)
      else ui().setStatus(msg('palette.needPanel', undefined, 'dialogs'), 'error')
    },
  },
  // ⌥ 点击的键盘等价物（issue #37 的「画布操作要有对象树 / inspector 等价
  // 路径」）：图内编辑态下选中一个元素，用它 bbox 的中心当那个点往后轮换。
  // 判据与动作都在 `canvas/interactions`，这里不判第二遍。
  {
    id: 'cycle-overlap',
    available: canCycleOverlapSelection,
    run: () => {
      // 几何权威没就位时 `cycleOverlapSelection` 什么都不动（ADR 0017），
      // 说一句「正在同步」而不是装作换了一个
      if (!cycleOverlapSelection()) {
        ui().setStatus(msg('status.geometrySyncing', undefined, 'workspace'), 'error')
      }
    },
  },
  {
    id: 'fit',
    shortcut: `${MOD}1`,
    run: () => {
      const page = useDocumentStore.getState().doc.page
      useViewportStore.getState().fitAnimated(page.w, page.h)
    },
  },
  { id: 'rulers', run: () => ui().setShowRulers(!ui().showRulers) },
  { id: 'grid', run: () => ui().setShowGrid(!ui().showGrid) },
  { id: 'canvas-settings', run: () => ui().setRightTab('canvas') },
  { id: 'left-assets', run: () => ui().setLeftTab('assets') },
  { id: 'left-elements', run: () => ui().setLeftTab('elements') },
  { id: 'left-layers', run: () => ui().setLeftTab('layers') },
  { id: 'shortcut-help', shortcut: '?', run: () => ui().setShortcutHelpOpen(true) },
]

/**
 * 命令名与搜索用的关键词。**收 t 而不是直接用模块级 translate**：这份列表
 * 在 useMemo 里算，只有把组件的 t 传进去，切语言时 memo 才会失效重算——
 * 否则搜索框里输入的中文关键词在英文界面下继续命中，反过来也一样。
 */
type Tr = (key: string) => string
const commandLabel = (t: Tr, id: string) => t(`palette.commands.${id}.label`)
const commandKeywords = (t: Tr, id: string) => t(`palette.commands.${id}.keywords`)

export function CommandPalette() {
  const { t } = useTranslation('dialogs')
  const open = usePalette((s) => s.open)
  const setOpen = usePalette((s) => s.setOpen)
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
  // 教程状态变了要重算可用命令（三条互斥）；项目开合决定项目命令出不出现
  const onboardingStatus = useOnboardingStore((s) => s.status)
  const projectPhase = useProjectStore((s) => s.phase)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
    const id = requestAnimationFrame(() => inputRef.current?.focus())
    // 兜底：焦点不在输入框（点了列表 / 空白）时 Esc 也要能关
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onEsc, true)
    return () => {
      cancelAnimationFrame(id)
      window.removeEventListener('keydown', onEsc, true)
    }
  }, [open, setOpen])

  // 搜索按**当前语言**的文案与关键词来：英文界面下输 "export" 能中，
  // 中文界面下输拼音首字母也能中
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const pool = COMMANDS.filter((c) => (!c.needsSelection || hasSelection) && (c.available?.() ?? true)).map(
      (c) => ({
        ...c,
        label: commandLabel(t, c.id),
        keywords: commandKeywords(t, c.id),
      }),
    )
    if (!q) return pool
    return pool.filter(
      (c) => c.label.toLowerCase().includes(q) || c.keywords.toLowerCase().includes(q),
    )
    // `onboardingStatus` / `projectPhase` 是让 memo 在状态变化时重算的信号，不是入参
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, hasSelection, t, onboardingStatus, projectPhase])

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, matches.length - 1)))
  }, [matches.length])

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-cmd-index="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const runCommand = (c: { run: () => void }) => {
    setOpen(false)
    c.run()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-ink/20 pt-[18vh] backdrop-blur-[1px]"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) setOpen(false)
      }}
    >
      <div className="w-[440px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-surface shadow-pop animate-pop-in">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Search size={14} className="shrink-0 text-ink-3" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Escape') setOpen(false)
              else if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActive((a) => Math.min(a + 1, matches.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActive((a) => Math.max(a - 1, 0))
              } else if (e.key === 'Enter' && matches[active]) {
                e.preventDefault()
                runCommand(matches[active])
              }
            }}
            placeholder={t('palette.placeholder')}
            aria-label={t('palette.searchLabel')}
            className="h-6 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-3"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {translate('keycap.esc')}
          </span>
        </div>
        <ul
          ref={listRef}
          className="max-h-72 overflow-y-auto py-1"
          role="listbox"
          aria-label={t('palette.listLabel')}
        >
          {matches.length === 0 && (
            <li className="px-3 py-2 text-xs text-ink-3">{t('palette.noMatch')}</li>
          )}
          {matches.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === active} data-cmd-index={i}>
              <button
                onPointerMove={() => setActive(i)}
                onClick={() => runCommand(c)}
                className={cn(
                  'flex h-7 w-full items-center gap-2 px-3 text-left text-xs',
                  i === active ? 'bg-accent-subtle text-accent' : 'text-ink',
                )}
              >
                <span className="min-w-0 flex-1 truncate">{c.label}</span>
                {c.shortcut && (
                  <span className="shrink-0 font-mono text-xs text-ink-3">{c.shortcut}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
