import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocale } from '@/i18n/react'
import { t as translate } from '@/i18n'
import {
  ChartLine,
  Crosshair,
  Eye,
  EyeOff,
  LayoutList,
  Lock,
  LockOpen,
  Ruler,
  SearchX,
  Shapes,
  TriangleAlert,
  Type,
  X,
  type IconComponent,
} from '@/components/ui/icons'
import { structuralParent } from '@/components/inspector/roles/hierarchy'
import { roleIcon } from '@/components/inspector/roles/roleIcons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { EditableFigureIcon } from '@/components/ui/semanticIcons'
import type { Manifest, ManifestElement, ManifestGroup } from '@/lib/api'
import { isElementHidden } from '@/canvas/interactions'
import { cn, isMac } from '@/lib/utils'
import { listRowClass } from '@/components/ui/listRow'
import { SearchInput } from '@/components/ui/SearchInput'
import { TreeChevron, TreeCount, TreeIcon, treeIndent } from '@/components/ui/TreeRow'
import {
  enterElementEdit,
  hideElement,
  toggleElementLocked,
  unhideElement,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, usePanelDisplayManifest, usePanelRender } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { untruncatedLabel } from '../inspector/identityCrumbs'
import { engineLabel, groupName, roleName, unsupportedOf } from '../inspector/roles/registry'
import { IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { BackgroundChoice } from '../inspector/ElementInspector'
import { MenuItem } from '../ui/Menu'
import { RowMenu } from '../ui/RowMenu'
import { useRowMenu } from '../ui/useRowMenu'
import { Tip } from '../ui/Tooltip'
import { isEffectiveOverrideAt } from '@/lib/effectiveOverride'

/**
 * 图内元素导航器。
 *
 * 由 manifest 的结构建树（figure → [组] → 子图 → 语义聚类 → 元素 → 零件）。父级只有
 * `roles/hierarchy.structuralParent` 一处：先认引擎给的显式 `parent_gid`（共享色条的
 * 组、单宿主色条挂回子图），再按 gid 路径回退。组是真实节点（可选中，选中 = 成员一起
 * 平移 / 缩放）；语义聚类（抽屉）只是视图容器，不可选中、不进面包屑。
 * 是柱形系列、刻度组、重叠元素这些「画布上点不准」元素的稳定选择入口。
 * 选中走 uiStore.selectedGids —— 与画布点击、ElementInspector、批量编辑同一条通路；
 * 隐藏/恢复走 visible override（非破坏、进撤销）；锁定写在 PanelObject.lockedGids 上。
 */

/** 树节点：真实元素、真实的组或语义聚类标题（聚类不可选中，只组织层级） */
interface TreeNode {
  el?: ManifestElement
  group?: ManifestGroup
  /** 聚类节点：labelKey 而不是成品文案——切语言时同一棵树要跟着换说法 */
  cluster?: { key: string; labelKey: string }
  children: TreeNode[]
}

const nodeKey = (n: TreeNode, parentKey = ''): string =>
  n.el ? n.el.gid : n.group ? n.group.gid : `${parentKey}#${n.cluster!.key}`

/** 这一行指代的真实节点（元素或组）；聚类行没有 */
const nodeGid = (n: TreeNode): string | undefined => n.el?.gid ?? n.group?.gid

/** 本组文案在 workspace:elementTree.* 下 */
const et = (key: string, values?: Record<string, unknown>) =>
  translate(`elementTree.${key}`, { ns: 'workspace', ...(values ?? {}) })

// 角色 → 图标：唯一出处在 roles/roleIcons（身份头共用）

/** 语义聚类：子图直属元素按角色归组，找不准的元素靠类别缩小范围 */
const CLUSTERS: { key: string; labelKey: string; icon: IconComponent; roles: Set<string> }[] = [
  { key: 'text', labelKey: 'groupText', icon: Type, roles: new Set(['text', 'title', 'axis_label']) },
  {
    key: 'series',
    labelKey: 'groupSeries',
    icon: ChartLine,
    roles: new Set(['line', 'scatter', 'bar_series', 'bar', 'errorbar', 'fill', 'image']),
  },
  { key: 'axis', labelKey: 'groupAxis', icon: Ruler, roles: new Set(['ticks', 'spine', 'grid']) },
  {
    key: 'legend',
    labelKey: 'groupLegend',
    icon: LayoutList,
    roles: new Set(['legend', 'legend_text', 'colorbar']),
  },
]
const clusterIcon = (key: string): IconComponent => CLUSTERS.find((c) => c.key === key)?.icon ?? Shapes

/**
 * 展示分类（抽屉）按元素**是什么**分，与它挂在谁下面无关：色条轴（单宿主色条挂回子图
 * 之后它就是子图的直接子节点）与图例同属「图例与色条」。
 */
const clusterOf = (el: ManifestElement): (typeof CLUSTERS)[number] | undefined =>
  CLUSTERS.find((c) => c.roles.has(el.is_colorbar ? 'colorbar' : el.role))

function buildTree(manifest: Manifest): TreeNode[] {
  const nodes = new Map<string, TreeNode>()
  for (const el of manifest.elements) nodes.set(el.gid, { el, children: [] })
  for (const group of manifest.groups ?? []) nodes.set(group.gid, { group, children: [] })
  const parentOf = structuralParent(manifest)
  const roots: TreeNode[] = []
  const placed = new Set<string>()
  const place = (gid: string) => {
    if (placed.has(gid)) return
    placed.add(gid)
    const p = parentOf(gid)
    // 组在它第一个成员出现的位置落座：整体顺序仍贴着引擎的元素序
    if (p && nodes.has(p) && !nodes.get(p)!.el) place(p)
    if (p && nodes.has(p)) nodes.get(p)!.children.push(nodes.get(gid)!)
    else roots.push(nodes.get(gid)!)
  }
  for (const el of manifest.elements) place(el.gid)

  // 子图直属元素按语义聚类；元素很少的子图不加聚类层
  for (const node of nodes.values()) {
    const role = node.el?.role
    if (role !== 'axes' && role !== 'axes3d') continue
    if (node.children.length <= 4) continue
    const buckets = new Map<string, TreeNode>()
    const next: TreeNode[] = []
    for (const child of node.children) {
      const c = child.el ? clusterOf(child.el) : undefined
      if (!c) {
        next.push(child)
        continue
      }
      let bucket = buckets.get(c.key)
      if (!bucket) {
        bucket = { cluster: { key: c.key, labelKey: c.labelKey }, children: [] }
        buckets.set(c.key, bucket)
        next.push(bucket)
      }
      bucket.children.push(child)
    }
    // 只有一个成员的聚类不值得多一层
    node.children = next.flatMap((n) =>
      n.cluster && n.children.length === 1 ? n.children : [n],
    )
  }
  return roots
}

interface Row {
  node: TreeNode
  depth: number
  key: string
  /** 同一层兄弟里的位置与个数：读屏念「第 2 项，共 5 项」（`aria-posinset` / `aria-setsize`） */
  pos: number
  size: number
}

function flatten(
  nodes: TreeNode[],
  depth: number,
  parentKey: string,
  isOpen: (n: TreeNode, key: string) => boolean,
  out: Row[],
): Row[] {
  nodes.forEach((n, i) => {
    const key = nodeKey(n, parentKey)
    out.push({ node: n, depth, key, pos: i + 1, size: nodes.length })
    if (n.children.length && isOpen(n, key)) flatten(n.children, depth + 1, key, isOpen, out)
  })
  return out
}

/** 命中搜索：标签 / 角色名 / gid（聚类节点按聚类名） */
function matches(n: TreeNode, q: string, manifest: Manifest): boolean {
  if (n.cluster) return et(n.cluster.labelKey).toLowerCase().includes(q)
  if (n.group) return groupName(n.group, manifest).toLowerCase().includes(q)
  const el = n.el!
  return (
    el.label.toLowerCase().includes(q) ||
    roleName(el.role).toLowerCase().includes(q) ||
    el.gid.toLowerCase().includes(q)
  )
}

/** 保留匹配节点与其祖先/后代的过滤树 */
function filterTree(nodes: TreeNode[], q: string, manifest: Manifest): TreeNode[] {
  const out: TreeNode[] = []
  for (const n of nodes) {
    if (matches(n, q, manifest)) {
      out.push(n) // 自身命中：整棵子树保留
      continue
    }
    const kids = filterTree(n.children, q, manifest)
    if (kids.length) out.push({ ...n, children: kids })
  }
  return out
}

/** 只看某分支：该 gid 的祖先链 + 其整棵子树 */
function isolateTree(nodes: TreeNode[], gid: string): TreeNode[] {
  const out: TreeNode[] = []
  for (const n of nodes) {
    if (nodeGid(n) === gid) {
      out.push(n)
      continue
    }
    const kids = isolateTree(n.children, gid)
    if (kids.length) out.push({ ...n, children: kids })
  }
  return out
}

/** 某个 gid 所在行的祖先 key 链（不含自己）；树里没有它就是 null */
function ancestorKeys(nodes: TreeNode[], gid: string, parentKey = ''): string[] | null {
  for (const n of nodes) {
    const key = nodeKey(n, parentKey)
    if (nodeGid(n) === gid) return []
    const below = ancestorKeys(n.children, gid, key)
    if (below) return [key, ...below]
  }
  return null
}

const canHide = (el: ManifestElement) =>
  el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')

/**
 * 行上显示的名字。刻度文字只显示**值**（`10` 而不是 `刻度 “10”`）：它们只出现在
 * 「X 轴刻度」组下面，组名已经说了它们是什么，每行再念一遍「刻度」是同一个词
 * 重复十几行（2026-09-13 审计 B44）。完整名字仍在 `title` 与可达名里。
 */
function rowLabel(el: ManifestElement): string {
  if (el.role === 'ticklabel') {
    const text = el.editable.find((f) => f.prop === 'text')?.value
    if (typeof text === 'string' && text.trim()) return text
  }
  // 引擎把引号里的文字截到 18 个字符（`Reaction time (mi…”`）；树行按可用宽度用 CSS 截断
  // （`truncate` + title 全文），不再按字数截（2026-09-14 审计 S15：380px 的抽屉里只用了一半宽）
  const text = el.editable.find((f) => f.prop === 'text' || f.prop === 'label')?.value
  return engineLabel(untruncatedLabel(el.label, typeof text === 'string' ? text : undefined))
}

/**
 * `chrome`：`drawer`（缺省，坐在 `LeftPanel` 里：标题行在上面，搜索行只留下边距）/ `bare`（别处借用，
 * 如 playground 的侧栏：上面没有标题行，搜索行四周自己留边）。2026-10-07 设计审计 §10.3。
 */
export function ElementTree({ chrome = 'drawer' }: { chrome?: 'drawer' | 'bare' } = {}) {
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedIds = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)

  // 目标面板：正在图内编辑的优先，其次画布上选中的 可参数化面板
  const panel = useMemo(() => {
    const byId = (id: string | null) => {
      const o = id ? objects.find((x) => x.id === id) : undefined
      return o?.type === 'panel' && o.script ? o : null
    }
    return byId(elementPanelId) ?? byId(selectedIds.at(-1) ?? null)
  }, [objects, elementPanelId, selectedIds])

  const manifest = usePanelDisplayManifest(panel)
  const render = usePanelRender(panel)
  const rendering = render?.status === 'rendering'

  if (!panel) {
    /**
     * 两种空态是两句不同的话（2026-09-13 审计 B05）：画布上**有**可编辑的图、只是
     * 没选中 → 给「选中一张」这一步；一张都没有 → 说清只有脚本生成的图才有图内
     * 对象，把人送去素材。此前不分这两种、也不给下一步，只有一句「选中一个可参数
     * 化面板」——「参数化」是实现词，用户读不出该做什么。
     */
    const editable = objects.filter((o): o is PanelObject => o.type === 'panel' && !!o.script)
    if (editable.length === 0) {
      return (
        <EmptyState
          icon={EditableFigureIcon}
          title={et('noEditableTitle')}
          hint={et('noEditableHint')}
          action={{ label: et('openAssets'), onClick: () => useUiStore.getState().setLeftTab('assets') }}
        />
      )
    }
    return (
      <EmptyState
        icon={EditableFigureIcon}
        title={et('noPanelTitle')}
        action={{
          label: et('locateEditable'),
          // 选中即够：这棵树的目标面板就是「选中的那张可编辑的图」，不必先进编辑态
          onClick: () => useSelectionStore.getState().set([editable[0].id]),
        }}
      />
    )
  }

  if (render?.error?.key === 'artifact.backgroundVisibilityRequired') {
    return <BackgroundChoice key={`${panel.id} ${renderKeyOf(panel)}`} panel={panel} />
  }

  // 「需要渲染一次」也是一种空态：全站只有 EmptyState 一种形态（宪法第五节；左栏审计 L34）。
  // 渲染中把动作换成一句现状
  if (!manifest) {
    return (
      <EmptyState
        icon={EditableFigureIcon}
        title={et('needRender', { name: panel.name ?? panel.fileId })}
        hint={rendering ? et('building') : undefined}
        action={rendering ? undefined : { label: et('load'), onClick: () => enterElementEdit(panel.id) }}
      />
    )
  }

  return <TreeView key={panel.id} panel={panel} manifest={manifest} chrome={chrome} />
}

function TreeView({
  panel,
  manifest,
  chrome,
}: {
  panel: PanelObject
  manifest: Manifest
  chrome: 'drawer' | 'bare'
}) {
  useTranslation('workspace')
  const selectedGids = useUiStore((s) => s.selectedGids)
  const [query, setQuery] = useState('')
  const [isolated, setIsolated] = useState<string | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const listRef = useRef<HTMLUListElement>(null)

  const tree = useMemo(() => buildTree(manifest), [manifest])
  const q = query.trim().toLowerCase()

  const shown = useMemo(() => {
    let nodes = tree
    if (isolated) nodes = isolateTree(nodes, isolated)
    if (q) nodes = filterTree(nodes, q, manifest)
    return nodes
  }, [tree, isolated, q, manifest])

  const isOpen = (n: TreeNode, key: string) => {
    // 搜索 / 只看分支时全部展开，否则命不中匹配项
    if (q || isolated) return open[key] ?? true
    // 默认展开到**语义聚类的成员**：整张图 → 子图 → 文字 / 数据系列 / 图例 / 坐标轴
    // 各自的直接成员都看得见；成员自己再带的一层（刻度组下的每个刻度文字、柱形系列
    // 下的每根柱、图例下的每一项）收起（2026-09-13 审计 B44：一进来就铺到叶子，
    // 「刻度」一词重复十几行，结构密度高过当前任务；只开两级又什么都看不见）
    // 组与子图同一待遇：它的直接成员（子图、共享的色条轴）默认看得见
    const role = n.el?.role
    return (
      open[key] ??
      (!!n.cluster || !!n.group || n.el?.gid === 'figure' || role === 'axes' || role === 'axes3d')
    )
  }
  const rows = useMemo(
    () => flatten(shown, 0, '', isOpen, []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shown, open, q, isolated],
  )

  // 画布 / 属性页上选中的元素可能藏在折叠的聚类或刻度组里（审计 T08）：
  // 先把它的祖先链全部展开，行出来之后再滚到它（但不抢焦点）
  const primaryGid = selectedGids.at(-1)
  useEffect(() => {
    if (!primaryGid) return
    const keys = ancestorKeys(tree, primaryGid)
    if (!keys?.length) return
    setOpen((s) => {
      if (keys.every((k) => s[k] === true)) return s
      const next = { ...s }
      for (const k of keys) next[k] = true
      return next
    })
  }, [primaryGid, tree])
  useEffect(() => {
    if (!primaryGid) return
    listRef.current
      ?.querySelector(`[data-el="${CSS.escape(primaryGid)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [primaryGid, rows])

  const focusRow = (key: string) =>
    listRef.current?.querySelector<HTMLElement>(`[data-el="${CSS.escape(key)}"]`)?.focus()

  /*
   * 行是 memo 的（见 ElementRow 的注释），交给行的回调必须在整棵树的生命期里是
   * 同一个引用——每次渲染新建一个箭头函数，memo 就形同虚设。行调用时带上自己的
   * key / gid / 当前展开态，树级回调不必按行闭包。
   */
  const rowsRef = useRef(rows)
  useLayoutEffect(() => {
    rowsRef.current = rows
  }, [rows])
  const moveFocus = useCallback((from: string, delta: number) => {
    const rs = rowsRef.current
    const next = rs[rs.findIndex((r) => r.key === from) + delta]
    if (next)
      listRef.current?.querySelector<HTMLElement>(`[data-el="${CSS.escape(next.key)}"]`)?.focus()
  }, [])
  // 行上的 expanded 就是 isOpen(node, key) 的结果：翻转它即可，不必再按行求一次
  const toggle = useCallback(
    (key: string, expanded: boolean) => setOpen((s) => ({ ...s, [key]: !expanded })),
    [],
  )

  /** 点树选中元素：未在编辑态则先进入（选中与画布/属性页共用同一条通路） */
  const panelId = panel.id
  const selectGid = useCallback(
    (gid: string, additive: boolean) => {
      const ui = useUiStore.getState()
      const select = () => {
        if (additive && gid !== 'figure') ui.toggleSelectedGid(gid)
        else ui.setSelectedGid(gid)
      }
      if (ui.elementPanelId === panelId) select()
      else {
        const entered = enterElementEdit(panelId)
        if (entered instanceof Promise) void entered.then(ok => { if (ok) select() })
        else if (entered) select()
      }
    },
    [panelId],
  )

  // 行上的「隐藏 / 锁定」按 gid 查这两张表。override 未渲染回来前也要即时反馈，
  // 所以隐藏同时认 visible=false 的 override 与 manifest 自己报的不可见
  const hiddenGids = useMemo(
    () =>
      new Set(
        panel.overrides
          // 认生效的那条（重复的旧条目被后面的 visible=true 遮住时不算隐藏）
          .filter(
            (o, i) =>
              o.prop === 'visible' && o.value === false && isEffectiveOverrideAt(panel.overrides, i),
          )
          .map((o) => o.gid),
      ),
    [panel.overrides],
  )
  const lockedGids = useMemo(() => new Set(panel.lockedGids ?? []), [panel.lockedGids])

  // 文案在 memo 里成文：换语言要重算（依赖里带上当前语言，Codex #832）
  const locale = useLocale()
  const isolatedLabel = useMemo(() => {
    if (!isolated) return ''
    const hit = manifest.elements.find((e) => e.gid === isolated)
    const group = manifest.groups?.find((g) => g.gid === isolated)
    return et('isolated', {
      label: hit ? engineLabel(hit.label) : group ? groupName(group, manifest) : isolated,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isolated, manifest, locale])

  // 没有选中时 primaryGid 是 undefined，而聚类行的 `el` 也是 undefined——直接比会停在第一个聚类行
  const focusKey =
    (primaryGid !== undefined ? rows.find((r) => nodeGid(r.node) === primaryGid)?.key : undefined) ??
    rows[0]?.key

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 搜索框下留白与素材页 / 画布页同一档 8px（左栏审计 L36）。「只看这一支」是搜索行里的一枚 chip，
          不再是搜索框下面另起的一条横幅（2026-10-07 设计审计 §10.3）：它与搜索一样是「看哪些行」的筛选 */}
      <div className={cn('flex shrink-0 items-center gap-1.5', chrome === 'bare' ? 'p-2' : 'px-3 pb-2')}>
        <SearchInput
          value={query}
          onValueChange={setQuery}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && rows.length) {
              e.preventDefault()
              focusRow(rows[0].key)
            }
          }}
          placeholder={et('search')}
          aria-label={et('searchAria')}
        />
        {isolated && (
          <span
            data-element-isolate
            className="flex h-7 min-w-0 max-w-[50%] shrink items-center gap-1 rounded-full bg-surface-hover pl-2 pr-0.5 text-xs text-ink"
          >
            <Crosshair size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
            <span className="min-w-0 truncate">{isolatedLabel}</span>
            <IconButton iconSize="xs" label={et('exitIsolate')} data-element-isolate-exit onClick={() => setIsolated(null)}>
              <X size={ICON_SIZE.xs} />
            </IconButton>
          </span>
        )}
      </div>

      <ul
        ref={listRef}
        role="tree"
        aria-label={et('listLabel')}
        className="min-h-0 flex-1 overflow-y-auto py-1"
      >
        {rows.length === 0 && (
          <li>
            <EmptyState icon={SearchX} title={et('noMatch')} />
          </li>
        )}
        {rows.map(({ node, depth, key, pos, size }) => {
          if (node.cluster) {
            return (
              <ClusterRow
                key={key}
                rowKey={key}
                label={et(node.cluster.labelKey)}
                icon={clusterIcon(node.cluster.key)}
                count={node.children.length}
                depth={depth}
                pos={pos}
                size={size}
                expanded={isOpen(node, key)}
                tabbable={focusKey === key}
                onToggle={toggle}
                onMoveFocus={moveFocus}
              />
            )
          }
          if (node.group) {
            const g = node.group
            return (
              <ElementRow
                key={key}
                rowKey={key}
                panelId={panelId}
                gid={g.gid}
                label={groupName(g, manifest)}
                role="group"
                name={groupName(g, manifest)}
                canHide={false}
                lockable={false}
                readonly={false}
                hidden={false}
                locked={false}
                depth={depth}
                pos={pos}
                size={size}
                selected={selectedGids.includes(g.gid)}
                tabbable={focusKey === key}
                expanded={node.children.length ? isOpen(node, key) : undefined}
                onToggle={toggle}
                onSelect={selectGid}
                onIsolate={setIsolated}
                onMoveFocus={moveFocus}
              />
            )
          }
          const el = node.el!
          return (
            <ElementRow
              key={key}
              rowKey={key}
              panelId={panelId}
              gid={el.gid}
              label={el.label}
              role={el.role}
              name={rowLabel(el)}
              canHide={canHide(el)}
              lockable={el.gid !== 'figure'}
              readonly={el.editable.length === 0}
              hidden={hiddenGids.has(el.gid) || isElementHidden(el)}
              locked={lockedGids.has(el.gid)}
              depth={depth}
              pos={pos}
              size={size}
              selected={selectedGids.includes(el.gid)}
              tabbable={focusKey === key}
              expanded={node.children.length ? isOpen(node, key) : undefined}
              onToggle={toggle}
              onSelect={selectGid}
              onIsolate={setIsolated}
              onMoveFocus={moveFocus}
            />
          )
        })}
      </ul>
    </div>
  )
}


/*
 * 两种行都是 memo 的，props 全是**这一行显示与交互实际用到的值**（字符串 / 布尔 /
 * 数字）加上树级稳定回调——不收整个 `panel`（每次 commit 都是新引用），也不收 `el`
 * （新图到达时 manifest 的每个元素都是新对象，而绝大多数行显示的东西一个字没变）。
 * 于是比较函数就是 React 默认的逐个 prop 浅比较：行里要用到 el 上的新东西，就只能
 * 先加成一个 prop，漏比某个字段这件事在结构上不会发生。
 * 行内的文案（可达名、菜单、「只读」）跟着语言走：每行各自 `useTranslation`，
 * 切语言时 memo 挡不住它。
 */

/** 聚类标题行：只组织层级，不可选中 */
const ClusterRow = memo(function ClusterRow({
  rowKey,
  label,
  icon,
  count,
  depth,
  pos,
  size,
  expanded,
  tabbable,
  onToggle,
  onMoveFocus,
}: {
  rowKey: string
  label: string
  icon: IconComponent
  count: number
  depth: number
  pos: number
  size: number
  expanded: boolean
  tabbable: boolean
  onToggle: (key: string, expanded: boolean) => void
  onMoveFocus: (from: string, delta: number) => void
}) {
  useTranslation('workspace')
  return (
    <li
      role="treeitem"
      aria-expanded={expanded}
      aria-level={depth + 1}
      aria-posinset={pos}
      aria-setsize={size}
      aria-label={et('groupAria', { label, count })}
      tabIndex={tabbable ? 0 : -1}
      data-el={rowKey}
      style={treeIndent(depth)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onMoveFocus(rowKey, e.key === 'ArrowDown' ? 1 : -1)
        } else if ((e.key === 'ArrowRight' && !expanded) || (e.key === 'ArrowLeft' && expanded) || e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onToggle(rowKey, expanded)
        }
      }}
      onPointerDown={(e) => {
        if (e.button === 0) onToggle(rowKey, expanded)
      }}
      className={cn(listRowClass({ muted: true }), 'pr-2')}
    >
      <TreeChevron expanded={expanded} />
      <TreeIcon icon={icon} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <TreeCount>{count}</TreeCount>
    </li>
  )
})

const ElementRow = memo(function ElementRow({
  rowKey,
  panelId,
  gid,
  label,
  role,
  name,
  canHide,
  lockable,
  readonly,
  hidden,
  locked,
  depth,
  pos,
  size,
  selected,
  tabbable,
  expanded,
  onToggle,
  onSelect,
  onIsolate,
  onMoveFocus,
}: {
  rowKey: string
  panelId: string
  gid: string
  /** 引擎原名（未翻译）：动作的撤销文案用它，显示前过 `engineLabel` */
  label: string
  role: string
  /** 行上显示的名字，`rowLabel(el)` 的结果 */
  name: string
  canHide: boolean
  /** 锁定写在 PanelObject.lockedGids、挡的是画布命中：整张图与组（画布上本来点不中）不给 */
  lockable: boolean
  readonly: boolean
  hidden: boolean
  locked: boolean
  depth: number
  pos: number
  size: number
  selected: boolean
  tabbable: boolean
  /** undefined = 叶子节点，无展开箭头 */
  expanded?: boolean
  onToggle: (key: string, expanded: boolean) => void
  onSelect: (gid: string, additive: boolean) => void
  onIsolate: (gid: string) => void
  onMoveFocus: (from: string, delta: number) => void
}) {
  useTranslation('workspace')
  const unsupported = unsupportedOf(role)
  const pointerFocusing = useRef(false)
  const shown = engineLabel(label)
  // ⋯ / 右键 / ⇧F10 同一份菜单（`ui/RowMenu`）；行有焦点时 ⋯ 进 Tab 顺序（此前 tabIndex=-1，键盘够不着）
  const menu = useRowMenu()

  return (
    <li
      {...menu.rowProps}
      role="treeitem"
      aria-selected={selected}
      aria-expanded={expanded}
      aria-level={depth + 1}
      aria-posinset={pos}
      aria-setsize={size}
      aria-label={
        et('rowAria', { label: shown, role: roleName(role) }) +
        (hidden ? et('rowAriaHidden') : '') +
        (locked ? et('rowAriaLocked') : '')
      }
      tabIndex={tabbable ? 0 : -1}
      data-el={rowKey}
      style={treeIndent(depth)}
      onFocus={(e) => {
        menu.rowProps.onFocus(e)
        if (e.target !== e.currentTarget || selected) return
        if (pointerFocusing.current) return
        // 焦点漫游即选中，与图层树一致
        onSelect(gid, false)
      }}
      onKeyDown={(e) => {
        menu.rowProps.onKeyDown?.(e)
        if (e.defaultPrevented || e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onMoveFocus(rowKey, e.key === 'ArrowDown' ? 1 : -1)
        } else if (e.key === 'ArrowRight' && expanded === false) {
          e.preventDefault()
          e.stopPropagation()
          onToggle(rowKey, expanded)
        } else if (e.key === 'ArrowLeft' && expanded === true) {
          e.preventDefault()
          e.stopPropagation()
          onToggle(rowKey, expanded)
        } else if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onSelect(gid, e.shiftKey || e.ctrlKey || e.metaKey)
        } else if (e.key === 'Delete' || e.key === 'Backspace') {
          e.preventDefault()
          e.stopPropagation()
          if (canHide && !hidden) hideElement(panelId, gid, label)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          useUiStore.getState().setSelectedGid(null)
          ;(e.currentTarget as HTMLElement).blur()
        }
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        // 先接住焦点，但让这次选择只由指针做一次：加减多选不能被 onFocus 重选。
        pointerFocusing.current = true
        e.currentTarget.focus({ preventScroll: true })
        pointerFocusing.current = false
        // Mac Control-click 是上下文菜单入口；接住焦点，但不改变已有选区。
        if (isMac && e.ctrlKey) return
        onSelect(gid, e.shiftKey || e.ctrlKey || e.metaKey)
      }}
      className={cn(listRowClass({ selected, hidden }), 'pr-0.5')}
    >
      <TreeChevron
        expanded={expanded}
        onToggle={expanded === undefined ? undefined : () => onToggle(rowKey, expanded)}
        label={expanded === undefined ? undefined : et(expanded ? 'collapse' : 'expand')}
      />
      <TreeIcon icon={roleIcon(role)} selected={selected} />

      <span className="min-w-0 flex-1 truncate" title={`${shown} · ${roleName(role)} · ${gid}`}>
        {name}
      </span>

      {unsupported && (
        <Tip
          label={et('unsupportedTip', {
            title: unsupported.title,
            reason: unsupported.reason,
          })}
          side="right"
        >
          <TriangleAlert size={ICON_SIZE.xs} className="shrink-0 text-ink-3" />
        </Tip>
      )}
      {/* 行选中时整行 500，行尾这个 meta 不跟着粗（同 L03 / L24 一族） */}
      {readonly && <span className="shrink-0 type-meta font-normal">{et('readonly')}</span>}

      {/* 锁定 / 隐藏状态常驻；动作本身收进 ⋯ 菜单 */}
      {locked && <Lock size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={et('lockedState')} />}
      {hidden && <EyeOff size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={et('hiddenState')} />}

      {/* 低频操作收进 ⋯（hover / 行有焦点时出现）；右键、⇧F10 开同一份。外面这层挡住按下：开菜单不改选区 */}
      <span className="flex shrink-0" onPointerDown={(e) => e.stopPropagation()}>
        <RowMenu state={menu} label={et('rowActions', { label: shown })} width={180} data-element-menu>
          <MenuItem icon={Crosshair} onSelect={() => onIsolate(gid)}>
            {et('isolateBranch')}
          </MenuItem>
          {lockable && (
            <MenuItem icon={locked ? LockOpen : Lock} onSelect={() => toggleElementLocked(panelId, gid, label)}>
              {et(locked ? 'unlock' : 'lock')}
            </MenuItem>
          )}
          {canHide && (
            <MenuItem
              icon={hidden ? Eye : EyeOff}
              // ⌫ 在这棵树上与画布上一样只「隐藏」（删除 = visible:false），不反向显示：已隐藏的行不标它
              shortcut={hidden ? undefined : '⌫'}
              onSelect={() => (hidden ? unhideElement(panelId, gid) : hideElement(panelId, gid, label))}
            >
              {et(hidden ? 'unhide' : 'hide')}
            </MenuItem>
          )}
        </RowMenu>
      </span>
    </li>
  )
})
