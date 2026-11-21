import { Fragment, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, OctagonAlert, File, FileExclamationPoint, Folder, Layers2, LayoutGrid } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Spinner } from '@/components/ui/Spinner'
import { listRowClass, rowMetaClass } from '@/components/ui/listRow'
import { MiddleEllipsis } from '@/components/ui/MiddleEllipsis'
import { TreeBranch } from '@/components/ui/TreeBranch'
import { TreeChevron, TreeCount, TreeIcon } from '@/components/ui/TreeRow'
import { isTreeNavKey, treeKeyAction } from '@/components/ui/treeKeys'
import { cn } from '@/lib/utils'
import { sameTable, tableKey, withOverview, type DataFileRow, type DataRow, type RowStatus, type TableRef } from '@/lib/dataFigure'
import type { AiDataOverview } from '@/lib/api'
import { useFormatMessage } from '@/i18n/react'
import type { UiMessage } from '@/i18n'

/**
 * 一次选多份、各画一张（ADR 0178 多选一节）：⌘ / Ctrl + 点加进 / 移出，Shift + 点选一段（按当前可见的表行顺序），
 * ⌘ / Ctrl + 空格切换，Esc 清掉；「选多份」多选态下普通单击 / Enter / 空格也是加选 / 取消。
 * 已选的行是白底浮起的卡片（`bg-surface` + 细描边，字重 medium），行尾行数换成对勾；行数任何时候都在。
 * 只有状态是 `table` 的行（含 xlsx 的工作表行）能进多选，别的行 ⌘ / Shift 点没有反应。
 * 开画之后行尾说这一份此刻的进度：等着（灰字）/ 转圈 / 已画（普通行）/ 没画出来（深一档的字 + 小感叹号，原因放 title；
 * 普通行，单击 = 开它的临时标签，单份流程自己会说清楚为什么）；状态按 `tableKey` 给，清单重读不丢。
 */
export interface DataListMulti {
  selected: ReadonlySet<string>
  /** 多选态（「选多份」按下）：普通单击 = 加选 / 取消、不开临时标签 */
  pickMode?: boolean
  /** 整批进行中：不再响应加选 / 取消（点行 = 开它的临时标签看候选） */
  running?: boolean
  rowStates: Record<string, { state: 'queued' | 'running' | 'done' | 'failed'; reason?: UiMessage | null }>
  onToggle: (ref: TableRef) => void
  onRange: (ref: TableRef, visible: TableRef[]) => void
  onClear: () => void
}

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
  onFocusRow,
  onConfirm,
  multi,
  floatInset,
}: {
  /** 清单底部有悬浮胶囊：滚到底时最后一行要能露在它上面（底部内边距 = 胶囊高度 + 离底边 + 呼吸） */
  floatInset?: boolean
  rows: DataFileRow[]
  selected: TableRef | null
  /** 用户改过表头行后，这一行说重读后的行数（键 = 路径） */
  overviews: Record<string, AiDataOverview | undefined>
  onSelect: (ref: TableRef) => void
  /** 键盘移到某个文件行（↑↓ / Tab）：卡片开着时预览跟着换（像访达的快速查看） */
  onFocusRow?: (ref: TableRef) => void
  /** 在已经选中的文件行上按 Enter：= 卡片的主按钮；返回 true 表示处理了 */
  onConfirm?: (ref: TableRef) => boolean
  /** 多选（不给 = 没有这个功能，行为与单选时逐字相同） */
  multi?: DataListMulti
}) {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const tree = useMemo(() => buildTree(rows), [rows])
  /** 纯平铺（没有文件夹也没有工作簿）：文件行不留树形箭头的空占位，图标左沿与标题文字左沿对齐 */
  const flat = useMemo(() => tree.every((n) => n.kind === 'file' && !n.file.workbook), [tree])
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

  /** 此刻可见的表行（展开着的文件夹 / 工作簿里、状态是 `table` 的），按画面顺序——Shift+点选一段按它 */
  const visibleTables = (): TableRef[] => {
    const out: TableRef[] = []
    const walk = (nodes: Node[]) => {
      for (const n of nodes) {
        if (n.kind === 'dir') {
          if (isOpen(n)) walk(n.children)
        } else if (n.file.workbook) {
          if (isOpen(n)) for (const sh of n.file.rows) if (statusOf(sh).kind === 'table') out.push(sh.ref)
        } else {
          const only = n.file.rows[0]
          if (only && statusOf(only).kind === 'table') out.push(only.ref)
        }
      }
    }
    walk(tree)
    return out
  }
  const isTable = (r: DataRow) => statusOf(r).kind === 'table'
  const stateOf = (r: DataRow) => multi?.rowStates[tableKey(r.ref)]?.state
  /** 此刻这一行点一下是加选 / 取消：多选态、表行、整批没在画、不是没画成的那几行（它们的出路是开临时标签） */
  const pickable = (r: DataRow) => !!multi?.pickMode && !multi.running && isTable(r) && stateOf(r) !== 'failed'
  /** 普通激活（单击 / Enter / 空格）：多选态下可选的表行 = 加选 / 取消、不开临时标签；其余照旧（再点已选中的行 = 确认） */
  const activate = (r: DataRow, on: boolean) => {
    if (pickable(r)) multi!.onToggle(r.ref)
    else if (!(on && onConfirm?.(r.ref))) onSelect(r.ref)
  }
  /** 点一行：⌘ / Ctrl / Shift 是多选（不可选的行没有反应；整批进行中也没有），其余是原来的单击 */
  const clickRow = (e: MouseEvent, r: DataRow) => {
    if (multi && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      if (!isTable(r) || multi.running) return
      if (e.shiftKey && !(e.metaKey || e.ctrlKey)) multi.onRange(r.ref, visibleTables())
      else multi.onToggle(r.ref)
      return
    }
    activate(r, false)
  }
  /** 这一行此刻是「已选的卡片」：在选择里，且不是已画 / 没画成的（它们回到普通行） */
  const isCard = (r: DataRow) => {
    const st = stateOf(r)
    return !!multi?.selected.has(tableKey(r.ref)) && st !== 'done' && st !== 'failed'
  }
  /** 行尾：这一份的出图进度 > 已选的对勾 > 灰字（行数 / 没认准 / 读不了） */
  const metaCell = (r: DataRow, text: string, on: boolean) => {
    const rs = multi?.rowStates[tableKey(r.ref)]
    const cls = cn(rowMetaClass(on), 'shrink-0 pr-1')
    if (rs?.state === 'queued')
      return (
        <span className={cls} data-studio-row-state="queued">
          {t('dataFigureV2.batch.queued')}
        </span>
      )
    if (rs?.state === 'running')
      return (
        <span className={cn(cls, 'flex items-center')} data-studio-row-state="running">
          <Spinner size={ICON_SIZE.xs} />
        </span>
      )
    if (rs?.state === 'done')
      return (
        <span className={cls} data-studio-row-state="done">
          {t('dataFigureV2.batch.done')}
        </span>
      )
    if (rs?.state === 'failed')
      return (
        <span
          className="flex shrink-0 items-center gap-1 pr-1 text-xs font-normal text-ink-2"
          data-studio-row-state="failed"
          title={rs.reason ? fm(rs.reason) : undefined}
        >
          <OctagonAlert size={ICON_SIZE.sm} aria-hidden />
          {t('dataFigureV2.batch.failed')}
        </span>
      )
    if (isCard(r))
      return (
        <span className={cn(cls, 'flex items-center text-ink-2')} data-studio-row-state="picked">
          <Check size={ICON_SIZE.xs} aria-hidden />
        </span>
      )
    return <span className={cls}>{text}</span>
  }

  /** 键盘：从 DOM 取当前可见行（收起中的子树 inert 不算）的层级与展开态，交给 `treeKeyAction`（图层树 / 元素树同一份） */
  const onKeyDown = (e: KeyboardEvent<HTMLElement>, activate: () => void, toggleSelf?: (open: boolean) => void, pick?: () => void) => {
    if (e.target !== e.currentTarget) return
    // ⌘ / Ctrl + 空格：把这一行加进 / 移出多选（只有表行传了 `pick`）
    if (e.key === ' ' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      pick?.()
      return
    }
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

  const rowShell = (selectedRow: boolean, card = false) =>
    cn(
      listRowClass({ size: 'sm', selected: selectedRow }),
      'mx-2 gap-1 pr-0.5',
      // 已选 = 白底浮起的卡片（与素材抽屉里的激活项同一种语言）：surface 底 + 细描边，字重 medium；相邻两行之间仍是原来的行间距
      card && 'bg-surface font-medium inset-ring inset-ring-border hover:bg-surface',
    )

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
                    aria-selected={on || isCard(sh)}
                    tabIndex={tabIndexFor(on)}
                    data-studio-sheet
                    data-studio-nav
                    data-studio-level={n.depth + 1}
                    data-kind={s.kind}
                    title={`${sh.ref.sheet} · ${m.title}`}
                    onClick={(e) => clickRow(e, sh)}
                    onFocus={(e) => e.target === e.currentTarget && onFocusRow?.(sh.ref)}
                    onKeyDown={(e) => onKeyDown(e, () => activate(sh, on), undefined, multi && !multi.running && s.kind === 'table' ? () => multi.onToggle(sh.ref) : undefined)}
                    className={cn(rowShell(on, isCard(sh)), sst.className)}
                    style={{ paddingLeft: 8 + (n.depth + 1) * 12, ...sst.style }}
                  >
                    <TreeChevron />
                    <TreeIcon icon={LayoutGrid} />
                    <span className="min-w-0 flex-1 truncate">{sh.ref.sheet}</span>
                    {metaCell(sh, m.text, on)}
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
        aria-selected={on || isCard(only)}
        tabIndex={tabIndexFor(on)}
        data-studio-file
        data-studio-nav
        data-studio-level={n.depth}
        data-kind={s.kind}
        title={`${baseName(f.path)} · ${m.title}`}
        onClick={(e) => clickRow(e, only)}
        onFocus={(e) => e.target === e.currentTarget && onFocusRow?.(only.ref)}
        onKeyDown={(e) => onKeyDown(e, () => activate(only, on), undefined, multi && !multi.running && s.kind === 'table' ? () => multi.onToggle(only.ref) : undefined)}
        className={cn(rowShell(on, isCard(only)), st.className)}
        style={{ paddingLeft: flat ? 4 : 8 + n.depth * 12, ...st.style }}
      >
        {!flat && <TreeChevron />}
        <TreeIcon icon={iconOf(s)} />
        <MiddleEllipsis text={baseName(f.path)} tail={8} className={cn('min-w-0 flex-1', s.kind === 'unreadable' && 'text-ink-3')} />
        {metaCell(only, m.text, on)}
      </li>
    )
  }

  return (
    <ul
      ref={nav}
      role="tree"
      aria-multiselectable={multi ? true : undefined}
      aria-label={t('dataFigureV2.list.heading')}
      onKeyDown={(e) => {
        // Esc 清掉多选
        // （有多选时这一下 Esc 归它：别再冒到临时标签的「Esc 关标签」）
        if (multi && e.key === 'Escape' && !e.defaultPrevented && (multi.selected.size > 0 || multi.pickMode)) {
          e.preventDefault()
          multi.onClear()
        }
      }}
      className={cn('flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto', floatInset ? 'pb-16' : 'pb-3')}
    >
      {tree.map((n, i) => renderNode(n, '', i))}
    </ul>
  )
}
