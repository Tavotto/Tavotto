import { useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { create } from 'zustand'
import { msg, t as translate } from '@/i18n'
import {
  AlignHorizontalDistributeCenter,
  AlignVerticalDistributeCenter,
  Bookmark,
  CircleQuestionMark,
  ClipboardList,
  Download,
  FolderOpen,
  FolderPlus,
  Fullscreen,
  Group,
  Images,
  Layers,
  Layers2,
  LayoutGrid,
  LayoutList,
  Lightbulb,
  Paintbrush,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  RotateCcwClock,
  Ruler,
  Save,
  Scaling,
  Search,
  SlidersHorizontal,
  SquareMousePointer,
  Tags,
  Type,
  Ungroup,
} from '@/components/ui/icons'
import type { ComponentType } from 'react'
import { KeyCaps } from '@/components/ui/Kbd'
import { Dialog } from '@/components/ui/Dialog'
import { StatusPill } from '@/components/ui/StatusPill'
import { keyOf } from '@/lib/keymap'
import { useWorkspaceStore } from '@/store/workspace'
import { canZoomToSelectionNow, fitStage, zoomToSelection } from '@/store/zoomToSelection'
import { objectLabel } from '@/types/document'
import { rankCommands, type PaletteSection } from '@/lib/commandRanking'
import { ICON_SIZE } from '@/components/ui/Icon'
import { cn } from '@/lib/utils'
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
  startNamedNode,
} from '@/store/actions'
import { canCycleOverlapSelection, cycleOverlapSelection } from '@/canvas/interactions'
import { resetHints, resetTutorial, runTutorialEntry, tutorialEntry } from '@/lib/onboarding/tutorial'
import { useDocumentStore } from '@/store/documentStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'

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
  // 导入即扫描（T02）的条被关掉之后，从这里重新打开：只是呈现，不开始新的扫描
  {
    id: 'project-scan',
    available: () => projectOpen() && useProjectScanStore.getState().scan !== null,
    run: () => useProjectScanStore.getState().reopen(),
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
  { id: 'export', shortcut: keyOf('export'), run: () => ui().setExportOpen(true) },
  { id: 'save-document', shortcut: keyOf('save'), run: () => void runManualSave() },
  { id: 'save-layout', shortcut: keyOf('saveAs'), run: () => ui().setLayoutOpen(true, 'save') },
  { id: 'load-layout', run: () => ui().setLayoutOpen(true, 'load') },
  { id: 'versions', shortcut: keyOf('timeline'), run: () => ui().setVersionsOpen(true) },
  { id: 'save-named-version', shortcut: keyOf('saveNamed'), run: startNamedNode },
  { id: 'styles', run: () => ui().setStylesOpen(true) },
  { id: 'new-doc', run: () => void newBlankDocument() },
  { id: 'add-text', shortcut: keyOf('toolText'), run: () => void addText() },
  { id: 'sub-labels', run: addSubLabels },
  { id: 'select-all', shortcut: keyOf('selectAll'), run: selectAll },
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
    shortcut: keyOf('zoomFit'),
    // 与 ⌘1 / 舞台双击同一个取景框：快速编辑里适应那张图（`stageFitFrame`）
    run: fitStage,
  },
  // 可用判据与动作同一个（`canZoomToSelectionNow`）：选中的全隐藏了 / 快速编辑里选区不含正在编辑的那张图
  // 不出现，不留一条静默空转、或去框一片看不见的版面的命令（Codex #833）
  {
    id: 'zoom-selection',
    available: canZoomToSelectionNow,
    shortcut: keyOf('zoomSelection'),
    run: () => void zoomToSelection(),
  },
  { id: 'rulers', run: () => ui().setShowRulers(!ui().showRulers) },
  { id: 'grid', run: () => ui().setShowGrid(!ui().showGrid) },
  { id: 'canvas-settings', run: () => ui().setRightTab('canvas') },
  { id: 'left-assets', run: () => ui().setLeftTab('assets') },
  { id: 'left-elements', run: () => ui().setLeftTab('elements') },
  { id: 'left-layers', run: () => ui().setLeftTab('layers') },
  { id: 'shortcut-help', shortcut: keyOf('help'), run: () => ui().setShortcutHelpOpen(true) },
]

/**
 * 命令前的图标：只从 `components/ui/icons` 里挑；没有合适图标的命令（全选、网格）不放，
 * 不新造。**只是外观**，命令清单与顺序仍由上面的 COMMANDS 与 `rankCommands` 决定。
 */
const COMMAND_ICONS: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  'refresh-project': RefreshCw,
  readiness: ClipboardList,
  'project-scan': ClipboardList,
  'tutorial-start': Play,
  'tutorial-resume': Play,
  'tutorial-reset': RotateCcw,
  'hints-reset': Lightbulb,
  export: Download,
  'save-document': Save,
  'save-layout': FolderPlus,
  'load-layout': FolderOpen,
  versions: RotateCcwClock,
  'save-named-version': Bookmark,
  styles: Paintbrush,
  'new-doc': Plus,
  'add-text': Type,
  'sub-labels': Tags,
  group: Group,
  ungroup: Ungroup,
  'layout-row': AlignHorizontalDistributeCenter,
  'layout-col': AlignVerticalDistributeCenter,
  'layout-grid': LayoutGrid,
  'edit-elements': SquareMousePointer,
  'cycle-overlap': Layers2,
  fit: Fullscreen,
  'zoom-selection': Scaling,
  rulers: Ruler,
  'canvas-settings': SlidersHorizontal,
  'left-assets': Images,
  'left-elements': LayoutList,
  'left-layers': Layers,
  'shortcut-help': CircleQuestionMark,
}

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
  const recent = useUiStore((s) => s.recentCommands)
  // 教程状态变了要重算可用命令（三条互斥）；项目开合决定项目命令出不出现
  const onboardingStatus = useOnboardingStore((s) => s.status)
  const projectPhase = useProjectStore((s) => s.phase)
  // 「缩放到选中」的可用判据看的是选区里**可见**的对象：隐藏 / 换选不一定改 ids 的长度
  const docObjects = useDocumentStore((s) => s.doc.objects)
  const selectionIds = useSelectionStore((s) => s.ids)
  // ……也看工作区模式：快速编辑里只认正在编辑的那张图（`zoomToSelection.selectionBoxOf`）
  const fastEditPanelId = useWorkspaceStore((s) => s.activePanelId)
  const [query, setQuery] = useState('')
  // 高亮行记的是**命令 id + 放置它时的查询**，不是下标（2026-09-16，学 beUI `useRowCursor`）。
  // 下标版的失败形状：↓↓ 停在第 3 行再多打一个字，列表换成另一组命令，高亮仍停在
  // 「第 3 行」——此时它指着一条用户没瞄准过的命令，回车就执行；钳位又落在提交之后的
  // 被动 effect 里，列表刚缩短那一帧 aria-selected 指向已不在的行。
  // 现在：查询一变光标自动失效、回到首行；行还在就跟着行走（重排也跟）；解析在 render 里做。
  const [cursor, setCursor] = useState<{ id: string; query: string } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // 每次打开都从空查询、首行开始。焦点进输入框、Esc / 点外面关闭、关闭后焦点还给打开前的元素——
  // 这三件都由 `Dialog chrome="palette"` 给（焦点陷阱与归还，2026-10-07 设计审计 §10.1），这里不再自己挂监听
  useEffect(() => {
    if (!open) return
    setQuery('')
    setCursor(null)
  }, [open])

  // 搜索按**当前语言**的文案与关键词来：英文界面下输 "export" 能中，
  // 中文界面下输拼音首字母也能中。顺序（选区 / 最近 / 常用 / 其他）由
  // `lib/commandRanking` 一处决定，有没有查询都一样；段标题只在空查询时显示
  // 查询按空白切成词，**每个词都要在 label 或 keywords 里命中，顺序不限**：「pdf 导出」
  // 也能中「导出 PDF」（此前整串 includes，词序反了就找不到）。顺序仍归 rankCommands。
  const q = query.trim().toLowerCase()
  const sections = useMemo(() => {
    const words = q.split(/\s+/).filter(Boolean)
    const pool = COMMANDS.filter((c) => (!c.needsSelection || hasSelection) && (c.available?.() ?? true)).map(
      (c) => ({
        ...c,
        label: commandLabel(t, c.id),
        keywords: commandKeywords(t, c.id),
      }),
    )
    const hit = words.length
      ? pool.filter((c) => {
          const hay = `${c.label} ${c.keywords}`.toLowerCase()
          return words.every((w) => hay.includes(w))
        })
      : pool
    return rankCommands(hit, { hasSelection, recent })
    // `onboardingStatus` / `projectPhase` / `docObjects` / `selectionIds` / `fastEditPanelId` 是让 memo 在状态变化时重算的信号，不是入参
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, hasSelection, recent, t, onboardingStatus, projectPhase, docObjects, selectionIds, fastEditPanelId])
  const matches = useMemo(() => sections.flatMap((s) => s.items), [sections])
  const showHeaders = !q

  // 光标 → 行号，在 render 里解析：查询变了或那一行已不在列表里，都回到首行
  const active = (() => {
    if (!cursor || cursor.query !== q) return 0
    const i = matches.findIndex((c) => c.id === cursor.id)
    return i < 0 ? 0 : i
  })()
  const moveTo = (i: number) => {
    const c = matches[i]
    if (!c || (cursor?.id === c.id && cursor.query === q)) return
    setCursor({ id: c.id, query: q })
  }

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-cmd-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active])

  const runCommand = (c: { id: string; run: () => void }) => {
    // 先把「面板已关」整个提交完（含 effect）再执行命令：Radix 的焦点陷阱要等 effect 才撤，
    // 命令若在同一次提交里把焦点交给别的表面（命名小框的 autoFocus），会被还没撤的陷阱当场拽回
    // 输入框——小框的 onBlur 随即把自己关掉，命令一闪而过（Codex #833）。退场后的焦点归还见 `ui/Dialog`
    flushSync(() => setOpen(false))
    useUiStore.getState().pushRecentCommand(c.id)
    c.run()
  }
  const sectionLabel = (s: PaletteSection) => t(`palette.section.${s}`)
  const activeId = matches[active] ? optionId(matches[active].id) : undefined

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t('palette.searchLabel')}
      chrome="palette"
      width={PALETTE_WIDTH}
      anchor="command-palette"
      initialFocusRef={inputRef}
    >
      {/* 输入行 48 / 输入 15（宪法第二十节命令面板那条的 2026-10-07 修订）。组合框语义：输入框是 combobox，
          结果列表是它控制的 listbox，高亮行经 aria-activedescendant 报给读屏——焦点始终留在输入框里 */}
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border px-4">
        <Search size={ICON_SIZE.md} className="shrink-0 text-ink-3" aria-hidden />
        <input
          ref={inputRef}
          data-palette-input
          role="combobox"
          aria-expanded
          aria-autocomplete="list"
          aria-controls={LIST_ID}
          aria-activedescendant={activeId}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              moveTo(Math.min(active + 1, matches.length - 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              moveTo(Math.max(active - 1, 0))
            } else if (e.key === 'Enter' && matches[active]) {
              e.preventDefault()
              runCommand(matches[active])
            }
          }}
          placeholder={t('palette.placeholder')}
          aria-label={t('palette.searchLabel')}
          className="h-6 min-w-0 flex-1 bg-transparent text-xl text-ink outline-none placeholder:text-ink-3"
        />
        {/* 选区上下文：命令作用在谁身上（「已选 2 个对象」/ 对象名）。不画 Esc 键帽：Esc 是所有对话框的约定 */}
        <SelectionChip />
      </div>
      <ul
        ref={listRef}
        id={LIST_ID}
        className="max-h-80 overflow-y-auto p-1"
        role="listbox"
        aria-label={t('palette.listLabel')}
      >
        {matches.length === 0 && (
          <li role="presentation" className="px-3 py-2 text-base text-ink-3">
            {t('palette.noMatch')}
          </li>
        )}
        {sections.map((section) => {
          const offset = matches.indexOf(section.items[0])
          return [
            showHeaders ? (
              <li
                key={`h:${section.section}`}
                role="presentation"
                data-palette-section={section.section}
                // 组头与菜单的 `MenuLabel` 同一格：12 / 400 / ink-3（比项淡一档，审计 M3）
                className="px-3 pb-1 pt-2.5 text-sm text-ink-3"
              >
                {sectionLabel(section.section)}
              </li>
            ) : null,
            ...section.items.map((c, j) => {
              const i = offset + j
              const CmdIcon = COMMAND_ICONS[c.id]
              return (
                <li
                  key={c.id}
                  id={optionId(c.id)}
                  role="option"
                  aria-selected={i === active}
                  data-cmd-index={i}
                  data-cmd-id={c.id}
                  data-active={i === active || undefined}
                  onPointerMove={() => moveTo(i)}
                  // 指针按下不抢焦点：焦点留在输入框里（组合框的约定），点击照常执行
                  onPointerDown={(e) => e.preventDefault()}
                  onClick={() => runCommand(c)}
                  // 行 32 / 13（宪法第二十节命令面板那条的 2026-10-07 修订）；选中行 `selected`（ink 10%）：
                  // `surface-2` 对白底只有 1.05:1，「现在会执行哪一条」几乎看不出来
                  className={cn(
                    'flex h-8 items-center gap-3 rounded-md px-2.5 text-base text-ink',
                    i === active && 'bg-selected',
                  )}
                >
                  {/* 图标槽定宽：没图标的命令（全选 / 网格）文字仍与别的行对齐 */}
                  <span className="flex w-4 shrink-0 justify-center text-ink-2" aria-hidden>
                    {CmdIcon && <CmdIcon size={ICON_SIZE.md} />}
                  </span>
                  <span data-cmd-label className="min-w-0 flex-1 truncate">
                    {c.label}
                  </span>
                  {c.shortcut && <KeyCaps keys={c.shortcut} className="shrink-0 flex-nowrap" />}
                </li>
              )
            }),
          ]
        })}
      </ul>
    </Dialog>
  )
}

/** 宪法第二十节：命令面板 520 宽 */
const PALETTE_WIDTH = 520
const LIST_ID = 'command-palette-list'
const optionId = (id: string) => `command-palette-option-${id}`

/** 输入行右端的选区上下文：选中一个对象时是它的名字，多个时是个数；没选中不占位 */
function SelectionChip() {
  const ids = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  if (ids.length === 0) return null
  const one = ids.length === 1 ? objects.find((o) => o.id === ids[0]) : null
  const text = one ? objectLabel(one) : translate('count.selectedObjects', { count: ids.length })
  return (
    <StatusPill data-palette-selection className="max-w-40 shrink-0">
      <span className="truncate">{text}</span>
    </StatusPill>
  )
}
