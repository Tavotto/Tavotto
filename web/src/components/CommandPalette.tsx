import { useEffect, useMemo, useRef, useState } from 'react'
import { create } from 'zustand'
import { Search } from 'lucide-react'
import { cn, MOD } from '@/lib/utils'
import {
  addSubLabels,
  addText,
  createLayoutGroup,
  enterElementEdit,
  groupSelected,
  newBlankDocument,
  selectAll,
  ungroupSelected,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
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

interface Command {
  id: string
  label: string
  /** 额外搜索词（拼音首字母 / 英文） */
  keywords: string
  shortcut?: string
  /** 需要选中对象才可用 */
  needsSelection?: boolean
  run: () => void
}

const ui = () => useUiStore.getState()

const COMMANDS: Command[] = [
  { id: 'export', label: '导出 PDF / PNG…', keywords: 'export pdf png dc', shortcut: `${MOD}E`, run: () => ui().setExportOpen(true) },
  { id: 'save-layout', label: '保存为画布文件…', keywords: 'save layout bc', shortcut: `${MOD}S`, run: () => ui().setLayoutOpen(true, 'save') },
  { id: 'load-layout', label: '载入画布文件…', keywords: 'open load layout zr', run: () => ui().setLayoutOpen(true, 'load') },
  { id: 'versions', label: '布局版本时间线…', keywords: 'version history timeline bb sjx', run: () => ui().setVersionsOpen(true) },
  { id: 'styles', label: '论文样式…', keywords: 'style token preset ys lwys', run: () => ui().setStylesOpen(true) },
  { id: 'new-doc', label: '新建空白文档', keywords: 'new blank xj', run: () => void newBlankDocument() },
  { id: 'add-text', label: '添加文字', keywords: 'text add tjwz', shortcut: 'T', run: () => void addText() },
  { id: 'sub-labels', label: '添加 (a)(b)(c) 序号标签', keywords: 'label abc xhbq', run: addSubLabels },
  { id: 'select-all', label: '全选', keywords: 'select all qx', shortcut: `${MOD}A`, run: selectAll },
  { id: 'group', label: '成组所选对象', keywords: 'group cz', needsSelection: true, run: groupSelected },
  { id: 'ungroup', label: '取消成组', keywords: 'ungroup qxcz', needsSelection: true, run: ungroupSelected },
  { id: 'layout-row', label: '创建行布局', keywords: 'row layout hbj', needsSelection: true, run: () => createLayoutGroup('row') },
  { id: 'layout-col', label: '创建列布局', keywords: 'col layout lbj', needsSelection: true, run: () => createLayoutGroup('col') },
  { id: 'layout-grid', label: '创建网格布局', keywords: 'grid layout wgbj', needsSelection: true, run: () => createLayoutGroup('grid') },
  {
    id: 'edit-elements',
    label: '编辑选中面板的图内元素',
    keywords: 'edit element tnys',
    needsSelection: true,
    run: () => {
      const id = useSelectionStore.getState().primary()
      const o = id ? useDocumentStore.getState().doc.objects.find((x) => x.id === id) : null
      if (o?.type === 'panel' && o.script) enterElementEdit(o.id)
      else ui().setStatus('先选中一个 可参数化面板', 'error')
    },
  },
  {
    id: 'fit',
    label: '缩放到适应画布',
    keywords: 'fit zoom syhb',
    shortcut: `${MOD}1`,
    run: () => {
      const page = useDocumentStore.getState().doc.page
      useViewportStore.getState().fit(page.w, page.h)
    },
  },
  { id: 'rulers', label: '显示 / 隐藏标尺', keywords: 'ruler bc', run: () => ui().setShowRulers(!ui().showRulers) },
  { id: 'grid', label: '显示 / 隐藏网格', keywords: 'grid wg', run: () => ui().setShowGrid(!ui().showGrid) },
  { id: 'canvas-settings', label: '画布设置', keywords: 'canvas page settings hbsz', run: () => ui().setRightTab('canvas') },
  { id: 'left-assets', label: '打开素材', keywords: 'asset panel sc', run: () => ui().setLeftTab('assets') },
  { id: 'left-elements', label: '打开图内元素树', keywords: 'element tree yss', run: () => ui().setLeftTab('elements') },
  { id: 'left-layers', label: '打开结构（图层）', keywords: 'layer structure tc jg', run: () => ui().setLeftTab('layers') },
  { id: 'shortcut-help', label: '快捷键帮助', keywords: 'shortcut help kjj', shortcut: '?', run: () => ui().setShortcutHelpOpen(true) },
]

export function CommandPalette() {
  const open = usePalette((s) => s.open)
  const setOpen = usePalette((s) => s.setOpen)
  const hasSelection = useSelectionStore((s) => s.ids.length > 0)
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

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const pool = COMMANDS.filter((c) => !c.needsSelection || hasSelection)
    if (!q) return pool
    return pool.filter(
      (c) => c.label.toLowerCase().includes(q) || c.keywords.toLowerCase().includes(q),
    )
  }, [query, hasSelection])

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, matches.length - 1)))
  }, [matches.length])

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-cmd-index="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const runCommand = (c: Command) => {
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
            placeholder="输入命令…"
            aria-label="搜索命令"
            className="h-6 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-3"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">Esc</span>
        </div>
        <ul ref={listRef} className="max-h-72 overflow-y-auto py-1" role="listbox" aria-label="命令">
          {matches.length === 0 && (
            <li className="px-3 py-2 text-xs text-ink-3">没有匹配的命令</li>
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
