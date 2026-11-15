import { Fragment, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { File, FileExclamationPoint, Folder, Layers2, LayoutGrid } from '@/components/ui/icons'
import { listRowClass, rowMetaClass } from '@/components/ui/listRow'
import { MiddleEllipsis } from '@/components/ui/MiddleEllipsis'
import { TreeBranch } from '@/components/ui/TreeBranch'
import { TreeChevron, TreeCount, TreeIcon } from '@/components/ui/TreeRow'
import { isTreeNavKey, treeKeyAction } from '@/components/ui/treeKeys'
import { cn } from '@/lib/utils'
import { sameTable, withOverview, type DataFileRow, type DataRow, type RowStatus, type TableRef } from '@/lib/dataFigure'
import type { AiDataOverview } from '@/lib/api'

/** 侧栏宽度：w-75 = 300px */
export const DATA_LIST_WIDTH_CLASS = 'w-75'

/** 子文件夹默认展开的数据文件总数上限：总数少于它全展开，多了只展开顶层（上百个文件时不一屏铺满） */
export const AUTO_EXPAND_BELOW = 30
/** 展开时只给前若干个子项做错峰淡入（几百行也不卡） */
const STAGGER_MAX = 8

type Node =
  | { kind: 'dir'; key: string; name: string; depth: number; children: Node[]; count: number }
  | { kind: 'file'; key: string; depth: number; file: DataFileRow }

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)

/** 路径清单 → 目录树：根目录的文件直接在顶层，目录各一个文件夹节点（嵌套按路径）；同层文件夹在前 */
function buildTree(rows: DataFileRow[]): Node[] {
  const root: Node = { kind: 'dir', key: '', name: '', depth: -1, children: [], count: 0 }
  for (const f of rows) {
    const parts = f.path.split('/')
    let cur = root
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts.slice(0, i + 1).join('/')
      let next = cur.children.find((c): c is Extract<Node, { kind: 'dir' }> => c.kind === 'dir' && c.key === key)
      if (!next) {
        next = { kind: 'dir', key, name: parts[i]!, depth: i, children: [], count: 0 }
        cur.children.push(next)
      }
      next.count += 1
      cur = next
    }
    cur.children.push({ kind: 'file', key: f.path, depth: parts.length - 1, file: f })
  }
  const order = (n: Node[]) => {
    n.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'dir' ? -1 : 1))
    for (const c of n) if (c.kind === 'dir') order(c.children)
  }
  order(root.children)
  return root.children
}

/**
 * 左列「数据」：一棵文件树（`role="tree"`）。文件夹行 = 箭头 + 文件夹图标 + 名字 + 行尾里面有几个数据文件，点整行展开 / 收起；
 * xlsx 与文件夹同一套展开交互（子项是工作表，行尾是它的行数）。文件行单行：类型图标 + 名字，行尾灰字写次要信息
 * （`32 行` / `没认准` / `读不了` / `没读`，完整信息进 title）。悬停 = 浅灰底，选中 = 更实的底（`listRowClass`）。
 * 键盘：↑ ↓ 走行、→ 展开、← 收起 / 回父、Enter / 空格 打开；展开 / 收起是高度过渡 + 子项错峰淡入（`TreeBranch` + `animate-rise-in`，
 * 减少动态效果时全局兜底直接切换）。行状态全由后端的清单给。**不把文件名放进 data-\***（诊断轨迹会记 data 属性）。
 */
export function DataList({
  rows,
  selected,
  overviews,
  onSelect,
}: {
  rows: DataFileRow[]
  selected: TableRef | null
  /** 用户改过表头行后，这一行说重读后的行数（键 = 路径） */
  overviews: Record<string, AiDataOverview | undefined>
  onSelect: (ref: TableRef) => void
}) {
  const { t } = useTranslation('ai')
  const tree = useMemo(() => buildTree(rows), [rows])
  /** 用户点过的展开态；没点过的按默认规则 */
  const [toggled, setToggled] = useState<Record<string, boolean>>({})
  /** 用户展开 / 收起过的（只有它们的子项做错峰淡入：首屏默认展开的不演一遍） */
  const [animated, setAnimated] = useState<ReadonlySet<string>>(new Set())
  const nav = useRef<HTMLUListElement>(null)

  const isOpen = (n: Node): boolean => {
    const key = n.kind === 'dir' ? `d:${n.key}` : `f:${n.key}`
    if (key in toggled) return toggled[key]!
    if (n.kind === 'dir') return n.depth === 0 || rows.length < AUTO_EXPAND_BELOW
    return false
  }
  const toggle = (key: string, now: boolean) => {
    setToggled((s) => ({ ...s, [key]: !now }))
    setAnimated((s) => new Set(s).add(key))
  }

  const statusOf = (r: DataRow): RowStatus => withOverview(r, overviews[r.ref.path])
  const meta = (s: RowStatus): { text: string; title: string } => {
    switch (s.kind) {
      case 'table':
        return { text: t('dataFigureV2.list.rows', { rows: s.rows }), title: t('dataFigureV2.list.fileMeta', { rows: s.rows, columns: s.columns }) }
      case 'unsure':
        return { text: t('dataFigureV2.list.unsure'), title: t('dataFigureV2.list.unsureTitle') }
      case 'unreadable':
        return { text: t('dataFigureV2.list.unreadable'), title: t('dataFigureV2.list.unreadable') }
      default:
        return { text: t('dataFigureV2.list.unread'), title: t('dataFigureV2.list.unread') }
    }
  }
  const iconOf = (s: RowStatus) => (s.kind === 'unreadable' ? FileExclamationPoint : s.kind === 'table' ? LayoutGrid : File)

  /** 键盘：从 DOM 取当前可见行（收起中的子树 inert 不算）的层级与展开态，交给 `treeKeyAction`（图层树 / 元素树同一份） */
  const onKeyDown = (e: KeyboardEvent<HTMLElement>, activate: () => void, toggleSelf?: (open: boolean) => void) => {
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      activate()
      return
    }
    if (!isTreeNavKey(e.key)) return
    const els = [...(nav.current?.querySelectorAll<HTMLElement>('[data-studio-nav]') ?? [])].filter((el) => !el.closest('[inert]'))
    const model = els.map((el) => ({
      level: Number(el.dataset.studioLevel),
      expanded: el.dataset.studioExpanded === undefined ? undefined : el.dataset.studioExpanded === 'true',
    }))
    const action = treeKeyAction(e.key, model, els.indexOf(e.currentTarget))
    if (!action) return
    e.preventDefault()
    if (action.type === 'focus') els[action.index]?.focus()
    else toggleSelf?.(action.type === 'collapse' ? true : false)
  }

  const rowShell = (selectedRow: boolean) => cn(listRowClass({ size: 'sm', selected: selectedRow }), 'mx-2 gap-1 pr-0.5')

  /** 子项错峰：前 STAGGER_MAX 个按序延后，之后一起；只在用户刚展开 / 收起的那一枝上 */
  const stagger = (parentKey: string, i: number) =>
    animated.has(parentKey)
      ? { className: 'animate-rise-in', style: { animationDelay: `calc(var(--duration-exit) * ${Math.min(i, STAGGER_MAX)})`, animationFillMode: 'backwards' } as const }
      : { className: undefined, style: undefined }

  let firstTabbable = true
  const tabIndexFor = (isSel: boolean) => {
    // 漫游 tabindex：选中的行是停靠点，没有选中的就是第一行
    if (selected ? isSel : firstTabbable) {
      firstTabbable = false
      return 0
    }
    return -1
  }

  const renderNode = (n: Node, parentKey: string, i: number): React.ReactNode => {
    const st = stagger(parentKey, i)
    if (n.kind === 'dir') {
      const open = isOpen(n)
      const key = `d:${n.key}`
      return (
        <Fragment key={key}>
            <li
              role="treeitem"
              aria-expanded={open}
              aria-level={n.depth + 1}
              tabIndex={tabIndexFor(false)}
              data-studio-folder
              data-studio-nav
              data-studio-level={n.depth}
              data-studio-expanded={open}
              title={n.name}
              onClick={() => toggle(key, open)}
              onKeyDown={(e) => onKeyDown(e, () => toggle(key, open), (o) => toggle(key, o))}
              className={cn(rowShell(false), st.className)}
              style={{ paddingLeft: 8 + n.depth * 12, ...st.style }}
            >
              <TreeChevron expanded={open} />
              <TreeIcon icon={Folder} />
              <span className="min-w-0 flex-1 truncate text-ink">{n.name}</span>
              <TreeCount>{n.count}</TreeCount>
            </li>
          <TreeBranch open={open}>
            {n.children.map((c, ci) => renderNode(c, key, ci))}
          </TreeBranch>
        </Fragment>
      )
    }
    const f = n.file
    const key = `f:${n.key}`
    if (f.workbook) {
      const open = isOpen(n)
      const pad = { paddingLeft: 8 + n.depth * 12 }
      return (
        <Fragment key={key}>
            <li
              role="treeitem"
              aria-expanded={f.rows.length > 0 ? open : undefined}
              aria-level={n.depth + 1}
              tabIndex={tabIndexFor(false)}
              data-studio-file
              data-studio-nav
              data-studio-level={n.depth}
              data-studio-expanded={f.rows.length > 0 ? open : undefined}
              data-kind="workbook"
              title={baseName(f.path)}
              onClick={() => (f.rows.length > 0 ? toggle(key, open) : onSelect({ path: f.path, sheet: null }))}
              onKeyDown={(e) =>
                onKeyDown(e, () => (f.rows.length > 0 ? toggle(key, open) : onSelect({ path: f.path, sheet: null })), (o) => f.rows.length > 0 && toggle(key, o))
              }
              className={cn(rowShell(false), st.className)}
              style={{ ...pad, ...st.style }}
            >
              <TreeChevron expanded={f.rows.length > 0 ? open : undefined} />
              <TreeIcon icon={Layers2} />
              <MiddleEllipsis text={baseName(f.path)} tail={8} className="min-w-0 flex-1" />
              <span className={cn(rowMetaClass(), 'shrink-0 pr-1')}>{f.unread ? t('dataFigureV2.list.unread') : t('dataFigureV2.list.sheets', { count: f.rows.length })}</span>
            </li>
          {f.rows.length > 0 && (
            <TreeBranch open={open}>
              {f.rows.map((sh, si) => {
                const s = statusOf(sh)
                const on = sameTable(selected, sh.ref)
                const m = meta(s)
                const sst = stagger(key, si)
                return (
                  <li
                    key={sh.ref.sheet}
                    role="treeitem"
                    aria-level={n.depth + 2}
                    aria-selected={on}
                    tabIndex={tabIndexFor(on)}
                    data-studio-sheet
                    data-studio-nav
                    data-studio-level={n.depth + 1}
                    data-kind={s.kind}
                    title={`${sh.ref.sheet} · ${m.title}`}
                    onClick={() => onSelect(sh.ref)}
                    onKeyDown={(e) => onKeyDown(e, () => onSelect(sh.ref))}
                    className={cn(rowShell(on), sst.className)}
                    style={{ paddingLeft: 8 + (n.depth + 1) * 12, ...sst.style }}
                  >
                    <TreeChevron />
                    <TreeIcon icon={LayoutGrid} />
                    <span className="min-w-0 flex-1 truncate">{sh.ref.sheet}</span>
                    <span className={cn(rowMetaClass(on), 'shrink-0 pr-1')}>{m.text}</span>
                  </li>
                )
              })}
            </TreeBranch>
          )}
        </Fragment>
      )
    }
    const only = f.rows[0]!
    const s = statusOf(only)
    const on = sameTable(selected, only.ref)
    const m = meta(s)
    return (
      <li
        key={key}
        role="treeitem"
        aria-level={n.depth + 1}
        aria-selected={on}
        tabIndex={tabIndexFor(on)}
        data-studio-file
        data-studio-nav
        data-studio-level={n.depth}
        data-kind={s.kind}
        title={`${baseName(f.path)} · ${m.title}`}
        onClick={() => onSelect(only.ref)}
        onKeyDown={(e) => onKeyDown(e, () => onSelect(only.ref))}
        className={cn(rowShell(on), st.className)}
        style={{ paddingLeft: 8 + n.depth * 12, ...st.style }}
      >
        <TreeChevron />
        <TreeIcon icon={iconOf(s)} />
        <MiddleEllipsis text={baseName(f.path)} tail={8} className="min-w-0 flex-1" />
        <span className={cn(rowMetaClass(on), 'shrink-0 pr-1')}>{m.text}</span>
      </li>
    )
  }

  return (
    <aside
      data-studio-data
      aria-label={t('dataFigureV2.list.heading')}
      className={cn('flex h-full shrink-0 flex-col border-r border-border bg-surface', DATA_LIST_WIDTH_CLASS)}
    >
      <div className="flex h-11 shrink-0 items-center px-4">
        <h2 className="type-section text-ink-3">{t('dataFigureV2.list.heading')}</h2>
      </div>
      <ul ref={nav} role="tree" aria-label={t('dataFigureV2.list.heading')} className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pb-3">
        {tree.map((n, i) => renderNode(n, '', i))}
      </ul>
    </aside>
  )
}
