import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatMessage, t as translate } from '@/i18n'
import {
  ChevronRight,
  Info,
  ListFilter,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  TriangleAlert,
  X,
  Zap,
  type IconComponent,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { EditableFigureIcon } from '@/components/ui/semanticIcons'
import {
  backendCodeMsg,
  backendErrorMsg,
  extOf,
  renderUrl,
  fileUrl,
  runtimePreviewUrl,
  type PanelInfo,
  type RuntimeAssetInfo,
  type UnsupportedAsset,
} from '@/lib/api'
import { runtimeSiblingOf } from '@/lib/assetSibling'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addFigureToLayout, openFastEdit } from '@/store/workspace'
import { startForeignConvert } from '@/lib/foreignEntry'
import { PENDING_STATUSES, reasonText, statusLabel } from '@/lib/readinessText'
import {
  DEFAULT_ASSET_FILTERS,
  useAssetBrowseStore,
  type AssetEditabilityFilter,
  type AssetFilters,
  type AssetSortKey,
  type AssetTypeFilter,
} from '@/store/assetBrowseStore'
import { assetFolders, folderLabel, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { isBusyPhase, useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyLine } from '../ui/EmptyLine'
import { SEARCH_MIN_ITEMS } from '../ui/searchThreshold'
import { MenuItem, PointMenu } from '../ui/Menu'
import { Notice, NoticeReveal } from '../ui/Notice'
import { splitNoticeText } from '@/lib/noticeText'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { Row } from '../ui/Field'
import { SearchInput } from '../ui/SearchInput'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { TruncateMiddle } from '../ui/TruncateMiddle'
import { DrawerHeaderActions } from './DrawerHeader'
import { ScriptLibrary } from './ScriptLibrary'

/** 面板文件名（带扩展名）：同 stem 的 PDF / PNG 靠它区分 */
const fileName = (id: string) => id.split('/').pop() ?? id
/** 格式名（不翻译）。位图按扩展名说实话：以前一律写 PNG，JPEG / TIFF 素材也被叫成 PNG */
const FORMAT_LABEL: Record<string, string> = { jpg: 'JPEG', jpeg: 'JPEG', tif: 'TIFF', tiff: 'TIFF' }
const formatOf = (p: PanelInfo) => {
  if (p.kind === 'pdf') return 'PDF'
  const ext = extOf(p.id)
  return FORMAT_LABEL[ext] ?? (ext.toUpperCase() || 'PNG')
}

type TypeFilter = AssetTypeFilter
type SortKey = AssetSortKey

/** 本组文案在 workspace:assets.* 下 */
const ab = (key: string, values?: Record<string, unknown>) =>
  translate(`assets.${key}`, { ns: 'workspace', ...(values ?? {}) })

const TYPE_VALUES: TypeFilter[] = ['all', 'pdf', 'raster', 'svg', 'runtime']
const SORT_VALUES: SortKey[] = ['name', 'recent', 'used']

/** PDF 是格式名，不翻译；其余按 key 取当前语言 */
const typeLabel = (v: TypeFilter) =>
  v === 'pdf' || v === 'svg'
    ? v.toUpperCase()
    : ab(v === 'all' ? 'typeAll' : v === 'raster' ? 'typeRaster' : 'typeRuntime')
const sortLabel = (v: SortKey) =>
  ab(v === 'name' ? 'sortName' : v === 'recent' ? 'sortRecent' : 'sortUsed')

const typeOptions = () => TYPE_VALUES.map((value) => ({ value, label: typeLabel(value) }))
const sortOptions = () => SORT_VALUES.map((value) => ({ value, label: sortLabel(value) }))

type Filters = AssetFilters

const DEFAULT_FILTERS = DEFAULT_ASSET_FILTERS

/**
 * 「图」区的一张卡：磁盘文件（FileAsset）或运行时图（RuntimeFigureAsset）。
 * runtime 条目可带 `sibling`——同一脚本、同一 stem 的磁盘图（见 `runtimeSiblingOf`）。
 */
type LibraryItem =
  | { kind: 'file'; panel: PanelInfo }
  | { kind: 'runtime'; asset: RuntimeAssetInfo; sibling?: PanelInfo }

const itemId = (it: LibraryItem) => (it.kind === 'file' ? it.panel.id : it.asset.id)

type Editability = Exclude<AssetEditabilityFilter, 'all'>

/**
 * 一张图归进「能不能编辑」的哪一组——**这是唯一的一份归组**（搜索框下那排胶囊用它）。
 *
 * 只翻译后端的事实，不另起判据：磁盘图按 `capability.status` 归——`editable` 是「可编辑」、
 * `layout_only` 是「仅版面」、`PENDING_STATUSES` 那四档是「待连接」（与横幅 / 接入中心同一个集合）。
 * 把待连接的并进「仅版面」是一句假话：卡上写着「需试运行」，下一步是跑一下，不是只能排版。
 * `capability` 缺席 = 这一轮还不知道（见 `PanelInfo.capability`）：有 `script` 就照卡片角标的老口径
 * 算可编辑，没有就**不归组**（返回 null，只在「全部」里出现）——绝不替后端补一个 `layout_only`。
 * 运行时图本身就出自脚本：跑过（有描述符）就能进图内编辑，没跑过是「待连接」——下一步是运行。
 */
function editabilityOf(it: LibraryItem): Editability | null {
  if (it.kind === 'runtime') return it.asset.descriptor ? 'editable' : 'pending'
  const status = it.panel.capability?.status
  if (status === undefined) return it.panel.script ? 'editable' : null
  if (status === 'editable') return 'editable'
  if (status === 'layout_only') return 'layout'
  return PENDING_STATUSES.includes(status) ? 'pending' : null
}

// 胶囊要放进 280px 的最窄抽屉（扣掉内边距只剩 256px），四档英文加起来不能超：所以用专用短标签，
// 不复用 statusLabel 的长句（"Layout only" / "To connect" 会把第四档挤出抽屉；Codex #1027）。
const editabilityLabel = (v: AssetEditabilityFilter) =>
  ab(
    v === 'editable'
      ? 'editabilityEditable'
      : v === 'layout'
        ? 'editabilityLayout'
        : v === 'pending'
          ? 'editabilityPending'
          : 'editabilityAll',
  )

export function AssetBrowser() {
  const panels = useAssetStore((s) => s.panels)
  const unsupported = useAssetStore((s) => s.unsupported)
  const loading = useAssetStore((s) => s.loading)
  const loaded = useAssetStore((s) => s.loaded)
  const error = useAssetStore((s) => s.error)
  const figuresDir = useAssetStore((s) => s.figuresDir)
  const recentlyUsed = useAssetStore((s) => s.recentlyUsed)
  const runtimeAssets = useRuntimeAssetStore((s) => s.assets)
  const objects = useDocumentStore((s) => s.doc.objects)
  const scriptCount = useScriptLibraryStore(
    (s) => (s.view?.all_scripts?.length ?? 0) + (s.view?.foreign_scripts?.length ?? 0),
  )
  const scriptsLoaded = useScriptLibraryStore((s) => s.loaded)
  const scriptsGeneration = useScriptLibraryStore((s) => s.generation)
  // 取失败（load 结束、没有清单）：脚本数是「不知道」，宁可摆搜索框。还没取到（含刚挂载、已触发未返回）则先不摆——
  // 先出现再收回会把页面推来推去；取得很快，不必为这一小段预留位置
  const scriptsFailed = useScriptLibraryStore((s) => !s.loaded && !s.loading && s.error !== null)

  // 搜索词与筛选住在组件外（`store/assetBrowseStore`）：切左轨页签 / 收起抽屉
  // 会把这个组件整个卸掉，放在组件 state 里的输入就跟着没了
  const query = useAssetBrowseStore((s) => s.query)
  const setQuery = useAssetBrowseStore((s) => s.setQuery)
  // 「刷新项目」按钮自己的忙碌态：它等的是 POST /api/project/refresh 走完
  // （静态扫描 + 合并注册表），而 assetStore 的 `loading` 只覆盖后半段的
  //  /api/panels。只看后者的话，按钮在最慢的那一步上是**不转**的。
  const [refreshing, setRefreshing] = useState(false)
  const filters = useAssetBrowseStore((s) => s.filters)
  const setFilters = useAssetBrowseStore((s) => s.setFilters)
  const scriptsOpen = useAssetBrowseStore((s) => s.scriptsOpen)
  const figuresOpen = useAssetBrowseStore((s) => s.figuresOpen)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [zoomed, setZoomed] = useState<LibraryItem | null>(null)
  /** 后端刷新与素材重取合起来才是用户眼里的「正在刷新」 */
  const busy = refreshing || loading
  /** 卡片右键 / ⇧F10 菜单：打开时的屏幕位置与它说的那一张卡 */
  const [cardMenu, setCardMenu] = useState<{ id: string; x: number; y: number } | null>(null)
  // 走**统一刷新**（后端一次完整的一轮），不是自己再扫一遍：
  // 「哪些文件是素材」「脚本怎么合进注册表」只有一份判据，事件与手动刷新共用它。
  const refreshNow = () => {
    setRefreshing(true)
    void refreshProjectNow()
      .catch((e: unknown) => useUiStore.getState().setStatus(backendErrorMsg(e), 'error'))
      .finally(() => setRefreshing(false))
    // runtime 图清单是另一个资源（不在 /api/panels 里），顺带取一次
    void useRuntimeAssetStore.getState().loadAssets()
  }

  const gridRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)

  // 脚本清单也由抽屉自己取，与脚本区展开与否无关：搜索框的阈值要数它，折叠着的区不挂载 ScriptLibrary，
  // 换项目又会清空清单——不取的话 scriptCount 会一直是 0，把长脚本列表唯一的搜索入口藏掉（Codex #1110 P2）
  useEffect(() => {
    if (!scriptsLoaded) void useScriptLibraryStore.getState().load()
    // 代际进依赖：A 项目取失败后切到 B，`loaded` 前后都是 false，只靠它 effect 不会重跑
  }, [scriptsLoaded, scriptsGeneration])

  // runtime 素材清单（只读端点）：面板挂载时取一次，之后靠 SSE 与 probe
  // 成功的副作用刷新
  useEffect(() => {
    if (useRuntimeAssetStore.getState().assets === null) {
      void useRuntimeAssetStore.getState().loadAssets()
    }
  }, [])

  /** 当前文档里每个素材各用了几次 */
  const usage = useMemo(() => {
    const map = new Map<string, number>()
    for (const o of objects) {
      if (o.type === 'panel') map.set(o.fileId, (map.get(o.fileId) ?? 0) + 1)
    }
    return map
  }, [objects])

  // 来源选项也收「用不了的文件」所在的目录：一个子目录里只有范围之外的 TIFF 时，它得选得中，
  // 否则那张清单按来源筛不出来（Codex #561）
  const folders = useMemo(() => assetFolders(panels, unsupported), [panels, unsupported])

  /** 用不了的文件跟着同一组搜索 / 来源 / 类型筛选走；「已使用」筛选下它们不可能出现 */
  const unsupportedShown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const { source: src, type: ty, usedOnly: used, editability: ed } = filters
    // 用不了的文件连排版都进不去，不属于「能不能编辑」的任何一组
    if (used || ed !== 'all' || (ty !== 'all' && ty !== 'raster' && ty !== 'svg')) return []
    return unsupported.filter(
      (u) =>
        (ty === 'all' || (extOf(u.id) === 'svg' ? ty === 'svg' : ty === 'raster')) &&
        (src === 'all' || u.folder === src) &&
        (!q || u.id.toLowerCase().includes(q) || u.name.toLowerCase().includes(q)),
    )
  }, [unsupported, query, filters])

  const { source, type, sort, usedOnly, editability } = filters
  const items = useMemo<LibraryItem[]>(() => {
    const q = query.trim().toLowerCase()
    let list = panels.filter((p) => {
      if (q && !p.id.toLowerCase().includes(q) && !p.name.toLowerCase().includes(q)) return false
      if (usedOnly && !usage.has(p.id)) return false
      if (source !== 'all' && p.folder !== source) return false
      if (type === 'pdf' && p.kind !== 'pdf') return false
      if (type === 'raster' && p.kind !== 'raster') return false
      if (type === 'svg' && p.kind !== 'svg') return false
      if (type === 'runtime') return false
      if (editability !== 'all' && editabilityOf({ kind: 'file', panel: p }) !== editability)
        return false
      return true
    })
    list = [...list]
    // 默认（按名称）排序把可参数化面板排在前面：它们才能进图内编辑，是这个
    // 面板里的一等公民。显式选了最近/次数排序时尊重用户的选择，不再分组。
    if (sort === 'name')
      list.sort(
        (a, b) =>
          Number(!!b.script) - Number(!!a.script) ||
          fileName(a.id).localeCompare(fileName(b.id)),
      )
    else if (sort === 'recent')
      list.sort((a, b) => (recentlyUsed[b.id] ?? 0) - (recentlyUsed[a.id] ?? 0))
    else list.sort((a, b) => (usage.get(b.id) ?? 0) - (usage.get(a.id) ?? 0))

    // 运行时图排在文件之后（它们没有 folder，被来源筛选与格式筛选排除）
    let rt = (runtimeAssets ?? []).filter((a) => {
      if (q && !a.stem.toLowerCase().includes(q) && !a.script.toLowerCase().includes(q))
        return false
      if (usedOnly && !usage.has(a.id)) return false
      if (source !== 'all') return false
      if (type !== 'all' && type !== 'runtime') return false
      if (editability !== 'all' && editabilityOf({ kind: 'runtime', asset: a }) !== editability)
        return false
      return true
    })
    rt = [...rt]
    if (sort === 'recent')
      rt.sort((a, b) => (recentlyUsed[b.id] ?? 0) - (recentlyUsed[a.id] ?? 0))
    else if (sort === 'used')
      rt.sort((a, b) => (usage.get(b.id) ?? 0) - (usage.get(a.id) ?? 0))
    else rt.sort((a, b) => a.stem.localeCompare(b.stem))

    // 同源的 runtime 条目紧跟在它的磁盘图后面（关系一眼可见）；其余运行时图
    // 仍排在文件之后。同源判据只看**当前显示的**文件：磁盘图被筛掉时 runtime
    // 条目回到末尾，不会指着一张看不见的卡
    const out: LibraryItem[] = []
    const placed = new Set<string>()
    for (const panel of list) {
      out.push({ kind: 'file', panel })
      for (const asset of rt) {
        if (placed.has(asset.id) || runtimeSiblingOf(asset, [panel]) !== panel) continue
        out.push({ kind: 'runtime', asset, sibling: panel })
        placed.add(asset.id)
      }
    }
    for (const asset of rt) {
      if (!placed.has(asset.id)) out.push({ kind: 'runtime', asset })
    }
    return out
  }, [panels, runtimeAssets, query, source, type, sort, usedOnly, editability, usage, recentlyUsed])

  // 「待连接」那一档只在项目里真有这样的图时出现（多数项目没有，常驻一个恒空的选项是噪音）；
  // 正选着它时不收——否则连好最后一张，选中的那一档凭空消失、清单却还是空的
  const hasPending = useMemo(
    () =>
      panels.some((panel) => editabilityOf({ kind: 'file', panel }) === 'pending') ||
      (runtimeAssets ?? []).some((asset) => editabilityOf({ kind: 'runtime', asset }) === 'pending'),
    [panels, runtimeAssets],
  )
  const editabilityValues: AssetEditabilityFilter[] =
    hasPending || editability === 'pending'
      ? ['all', 'editable', 'pending', 'layout']
      : ['all', 'editable', 'layout']

  // 列数按实测宽度算：抽屉最窄 280px 时也是双列（每张卡 ~125px，预览 3:2 约 83px 高）——
  // 这是素材库而不是看图器，同屏能扫到的张数比单张预览的尺寸重要；看细节有空格键放大。
  // 只有被挤到 <250px（覆盖式抽屉的极端情况）才退回单列
  useLayoutEffect(() => {
    const el = gridRef.current
    if (!el) return
    const measure = () => setColumns(el.clientWidth < 250 ? 1 : 2)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (activeId && !items.some((it) => itemId(it) === activeId)) setActiveId(null)
  }, [items, activeId])

  const focusCard = (id: string) => {
    setActiveId(id)
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)
      ?.focus({ preventScroll: false })
  }

  const move = (from: string, delta: number) => {
    const i = items.findIndex((it) => itemId(it) === from)
    const next = items[i + delta]
    if (next) focusCard(itemId(next))
  }

  // 标题行两颗动作钮的对象：选中的那张卡；没有描述符的运行时图没有可编辑 / 可添加的东西
  const activeItem = items.find((it) => itemId(it) === activeId)
  const activeActionable = !!activeItem && (activeItem.kind === 'file' || !!activeItem.asset.descriptor)
  const activeCap = activeItem?.kind === 'file' ? activeItem.panel.capability : undefined
  const activeForeign = activeCap?.foreign_source ?? null
  const activeName = activeItem ? (activeItem.kind === 'file' ? fileName(activeItem.panel.id) : activeItem.asset.stem) : ''

  const focusId = items.some((it) => itemId(it) === activeId)
    ? activeId
    : items[0]
      ? itemId(items[0])
      : undefined

  /** 非默认筛选条件 → 可移除的标签 */
  const chips: { key: keyof Filters; label: string }[] = []
  if (source !== 'all') chips.push({ key: 'source', label: folderLabel(source) })
  if (type !== 'all') chips.push({ key: 'type', label: typeLabel(type) })
  if (usedOnly) chips.push({ key: 'usedOnly', label: ab('usedChip') })
  if (sort !== 'name') chips.push({ key: 'sort', label: sortLabel(sort) })

  const clearChip = (key: keyof Filters) =>
    setFilters((f) => ({ ...f, [key]: DEFAULT_FILTERS[key] }))

  // 搜索时两个区都强制展开——匹配项可能就在收着的那个区里，收着的区域会让
  // 「没有结果」成为一句假话
  const searching = query.trim() !== ''
  const figuresShown = figuresOpen || searching
  const scriptsShown = scriptsOpen || searching
  // 区头上的计数：未筛选时就是全部素材数；筛选中写成「3 / 7」——此前这个比值单独占
  // 一行「文件夹信息 … 3」页脚，与「图 3」是同一个数说两遍（左栏审计 L04）
  const total = panels.length + (runtimeAssets?.length ?? 0)
  // 项目里一张图都没有时不写「图 0」：下面那句空态就是「项目里还没有图」，一行之隔说两遍
  // （同 L04）。筛到 0 条是另一回事——「0 / 7」告诉你还有 7 张只是没匹配上，那是有信息的
  const figuresCount =
    !loaded || total === 0 ? undefined : items.length === total ? total : `${items.length} / ${total}`

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 两颗同权重的图标钮（筛选 / 刷新）进标题行的动作槽（2026-10-07 设计审计 §10.3）；
          「只看可编辑」从这里的图标钮换成了搜索框下那排胶囊——藏在图标里没人找得到。都是 IconButton：28px、透明底、hover 才浮出，名字与气泡同一份；
          图标走默认的 16px——与面板头的钉、画布页的「+」同一档（左栏审计 L13） */}
      <DrawerHeaderActions>
        {/* 选中那张卡的两个动作：listbox 之外、可聚焦、带明确标签的真按钮（asset-library.md「素材卡的两个动作」）。
            住在标题行已有的工具区里——位置固定、不占新高度、不是常驻页脚；没选中卡 / 选中的没有可编辑内容时置灰。
            可见的 ＋ 与快捷键（Enter / Shift+Enter）落到同一对 action 上 */}
        <IconButton
          label={ab('openFigure')}
          tip={activeActionable ? ab('openAria', { name: activeName }) : ab('openFigure')}
          disabled={!activeActionable}
          data-asset-open
          onClick={() => void openFastEdit(activeId!)}
        >
          <Pencil size={ICON_SIZE.md} className="text-ink-2" />
        </IconButton>
        <IconButton
          label={ab('addToCanvas')}
          tip={activeActionable ? ab('addAria', { name: activeName }) : ab('addToCanvas')}
          disabled={!activeActionable}
          data-asset-add
          onClick={() => void addFigureToLayout(activeId!)}
        >
          <Plus size={ICON_SIZE.md} className="text-ink-2" />
        </IconButton>
        <FilterButton
          filters={filters}
          folders={folders}
          figuresDir={figuresDir}
          activeCount={chips.length}
          onChange={setFilters}
        />
        <IconButton
          label={ab('refresh')}
          tip={ab('refreshTip')}
          disabled={refreshing}
          aria-busy={busy || undefined}
          data-asset-refresh
          onClick={refreshNow}
        >
          {/* 自旋的是这个图标本身：Button 自带的 loading 会再插一个
              LoaderCircle，28px 的图标按钮里挤两个图标就是布局跳变 */}
          <RefreshCw size={ICON_SIZE.md} className={busy ? 'animate-spin text-ink-3' : 'text-ink-2'} />
        </IconButton>
      </DrawerHeaderActions>
      <div className="flex flex-col gap-1.5 px-3 pb-2">
        {/* 条目少于 6 个时不显示搜索框（有搜索词时保留）。这个框同时过滤图、用不了的文件和下面的脚本区，
            所以阈值按三者合计算——图少脚本多的项目里它是长脚本列表唯一的搜索入口（Codex #1110 P2） */}
        {/* 脚本清单：加载中先不摆（结果出来再定，不先出现再收回）；取失败是「不知道」不是 0，摆出来，不藏掉唯一的入口 */}
        {/* 已知的图 + 不支持的文件自己就 ≥ 6：不必等脚本清单（/api/registry 慢或挂住时也不藏） */}
        {(total + unsupported.length >= SEARCH_MIN_ITEMS ||
          scriptsFailed ||
          (scriptsLoaded && total + unsupported.length + scriptCount >= SEARCH_MIN_ITEMS) ||
          query !== '') && (
          <SearchInput
            value={query}
            onValueChange={setQuery}
            placeholder={ab('search')}
            aria-label={ab('searchAria')}
          />
        )}

        {/* 能不能编辑：一排常驻的胶囊（全部 / 可编辑 / [待连接] / 仅版面）。可编辑与仅排版的图在网格里
            混在一起时，靠卡上那个小角标一张张认太累。它是一眼可见的取值，所以不再进下面那排
            可移除的筛选标签。显示规则：有图、或有用不了的文件（它们只在「全部」下列出，得给入口）、
            或正选着非「全部」时都出；正选着非默认值时绝不藏控件，否则一次刷新让最后一张图消失就把
            用户困在「没有匹配的图」里、回不到「全部」（Codex #1027）。三者皆无才不出 */}
        {(total > 0 || unsupported.length > 0 || editability !== 'all') && (
          <Segmented<AssetEditabilityFilter>
            value={editability}
            onChange={(v) => setFilters((f) => ({ ...f, editability: v }))}
            ariaLabel={ab('editabilityAria')}
            items={editabilityValues.map((value) => ({ value, label: editabilityLabel(value) }))}
            data-testid="asset-editability"
          />
        )}

        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1" aria-label={ab('activeFilters')}>
            {chips.map((c) => (
              <button
                key={c.key}
                onClick={() => clearChip(c.key)}
                aria-label={ab('removeFilter', { label: c.label })}
                // 筛选 chip 是胶囊：白底 + 一圈细边（2026-10-07 设计审计 §10.3；chip 一律 rounded-full）
                className={cn(
                  'flex h-6 items-center gap-1 rounded-full bg-surface pl-2 pr-1.5 text-xs text-ink inset-ring inset-ring-border',
                  'outline-none transition-colors duration-fast hover:bg-surface-2 focus-visible:focus-ring',
                )}
              >
                {c.label}
                <X size={ICON_SIZE.xs} className="text-ink-3" />
              </button>
            ))}
          </div>
        )}
      </div>

      {busy && loaded && (
        <p className="px-3 py-1 text-xs text-ink-3" aria-live="polite" data-asset-refreshing>
          {ab('refreshing')}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ---- 图：FileAsset + RuntimeFigureAsset ---- */}
        {/* 「图」与「脚本」是同级的两个区，同一副可折叠区头（左栏审计 L06，拍板取 a）；
            图默认展开、带计数 */}
        <SectionToggle
          label={ab('sectionFigures')}
          count={figuresCount}
          open={figuresShown}
          onToggle={() => useAssetBrowseStore.getState().setFiguresOpen(!figuresOpen)}
          controls="asset-figures-section"
        />
        {/* 读不出 / 刷新失败**不收进可折叠的内容里**：图区收着时，头部「刷新」后端那一轮成了、
            `/api/panels` 却失败——`assetStore.load()` 吞掉错误返回 null，`refreshProjectNow()` 照常
            resolve、不弹提示，而这条 Notice 若在 hidden 的网格里，用户只看到图标停转（Codex #832）。
            错误挂在区头下面、不跟着折叠走，展开与否都看得见 */}
        {/* 事后才冒出来的一条：包 NoticeReveal，下面的网格是顺着滑下去的而不是瞬间跳一格 */}
        <NoticeReveal open={!!error}>
          <div className="px-3 pb-2">
            {/* 刷新失败：一条 Notice 坐在它说的那一区里（此前是贴着工具栏的一条红带）；
                加载过就照旧列保留下来的那份，卡片不消失。一句话的原因留在正文，长的进「详情」 */}
            {loaded ? (
              <Notice
                tone="danger"
                role="status"
                data-asset-refresh-failed
                title={ab('refreshFailed')}
                detail={splitNoticeText(error).detail}
              >
                {splitNoticeText(error).inline}
              </Notice>
            ) : (
              // 首次读不出：同一条一行 Notice（一句话 + 重试），后端原文进「详情」，不再整屏空态里摆一段原始文本
              <Notice
                tone="danger"
                data-asset-load-failed
                title={ab('loadFailed')}
                detail={error}
                action={
                  <Button
                    size="sm"
                    // 首次加载失败后的重试：**强制**另起一次，不复用可能同样
                    // 失败的那个在途请求——用户点重试的原因正是"刚才没成"
                    onClick={() => void useAssetStore.getState().load({ force: true })}
                  >
                    {ab('retry')}
                  </Button>
                }
              />
            )}
          </div>
        </NoticeReveal>
        {/* 网格容器常驻（收起时只是 hidden）：列数由它的实测宽度决定，ResizeObserver
            只在挂载时接一次，卸了再挂就量不到了；display:none 报 0 宽 → 单列，再展开时
            报回真实宽度 → 双列，同一个观察者两边都接得住 */}
        <div id="asset-figures-section" ref={gridRef} className="px-3 pb-2" hidden={!figuresShown}>
          {!loaded && !error && <GridSkeleton columns={columns} />}

          {/* 用不了的文件也是「有东西可说」：只有一张范围之外的 TIFF 的项目不是「还没有图」，
              只搜中了它也不是「没有匹配」——空态与下面那张清单不同时出现 */}
          {loaded && !error && items.length === 0 && unsupportedShown.length === 0 && (
            query || chips.length || editability !== 'all' ? (
              <EmptyLine
                data-asset-no-match
                action={{
                  label: translate('actions.clearSearch'),
                  onClick: () => {
                    setQuery('')
                    setFilters(DEFAULT_FILTERS)
                  },
                }}
              >
                {ab('noMatch')}
              </EmptyLine>
            ) : (
              /* 一句话 + 一个动作；「把文件放进目录」的说明在 title 里，不常驻 */
              <EmptyLine data-asset-empty title={ab('emptyHint')}>
                {ab('emptyTitle')}
              </EmptyLine>
            )
          )}

          {items.length > 0 && (
            <ul
              role="listbox"
              aria-label={ab('listLabel')}
              className="grid gap-2"
              style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
            >
              {items.map((it) =>
                it.kind === 'file' ? (
                  <AssetCard
                    key={it.panel.id}
                    panel={it.panel}
                    used={usage.get(it.panel.id) ?? 0}
                    selected={activeId === it.panel.id}
                    tabbable={focusId === it.panel.id}
                    onSelect={() => setActiveId(it.panel.id)}
                    onOpen={() => openFastEdit(it.panel.id)}
                    onAdd={() => addFigureToLayout(it.panel.id)}
                    onZoom={() => setZoomed(it)}
                    onMove={(d) => move(it.panel.id, d)}
                    onMenu={(at) => setCardMenu({ id: it.panel.id, ...at })}
                    columns={columns}
                  />
                ) : (
                  <RuntimeAssetCard
                    key={it.asset.id}
                    asset={it.asset}
                    sibling={it.sibling}
                    used={usage.get(it.asset.id) ?? 0}
                    selected={activeId === it.asset.id}
                    tabbable={focusId === it.asset.id}
                    onSelect={() => setActiveId(it.asset.id)}
                    onAdd={() => addFigureToLayout(it.asset.id)}
                    onZoom={() => setZoomed(it)}
                    onMove={(d) => move(it.asset.id, d)}
                    onMenu={(at) => setCardMenu({ id: it.asset.id, ...at })}
                    columns={columns}
                  />
                ),
              )}
            </ul>
          )}

          {/* 选中的图旁边有同名 R / MATLAB 脚本（后端报的 `foreign_source`）：一句话 + 一个主按钮，走主页那张卡的同一个转换入口。
              在 listbox 之外（option 里不许再嵌可 Tab 的控件） */}
          {activeForeign && (
            <div className="mt-2 flex items-center gap-2" data-asset-foreign={activeForeign.script}>
              <p className="min-w-0 flex-1 text-xs text-ink-2">{reasonText(activeCap!)}</p>
              <Button
                variant="primary"
                size="sm"
                data-asset-foreign-convert={activeForeign.language}
                onClick={() => startForeignConvert(activeForeign)}
              >
                {translate('prep.convert.btn.start', { ns: 'workspace' })}
              </Button>
            </div>
          )}

          {/* 与素材卡同一条规则：加载过就显示保留下来的那份——刷新失败时卡片还在，这张清单也在 */}
          {loaded && unsupportedShown.length > 0 && (
            <UnsupportedAssets items={unsupportedShown} />
          )}
        </div>

        {/* ---- 脚本：普通入口的「运行并发现图」住在这里 ---- */}
        {/* 图是主区域，脚本是可收起的第二层（审计 B07） */}
        <SectionToggle
          label={ab('sectionScripts')}
          open={scriptsShown}
          onToggle={() => useAssetBrowseStore.getState().setScriptsOpen(!scriptsOpen)}
          controls="asset-scripts-section"
        />
        {scriptsShown && (
          <div id="asset-scripts-section">
            <ScriptLibrary query={query} />
          </div>
        )}
      </div>

      {/* 卡片的全部动作在右键 / ⇧F10 菜单里（2026-10-16：页脚那条常驻动作栏去掉了——同一对动作在卡片上有
          就近入口、键盘有 Enter / Shift+Enter，页脚是第三处）。菜单挂在 listbox 之外，option 里不嵌交互控件 */}
      {cardMenu && (
        <AssetCardMenu
          item={items.find((it) => itemId(it) === cardMenu.id)}
          panel={panels.find((p) => p.id === cardMenu.id)}
          at={cardMenu}
          onClose={() => {
            setCardMenu(null)
            focusCard(cardMenu.id)
          }}
        />
      )}

      <Dialog
        open={!!zoomed}
        onOpenChange={(v) => !v && setZoomed(null)}
        title={zoomed ? (zoomed.kind === 'file' ? fileName(zoomed.panel.id) : zoomed.kind === 'runtime' ? zoomed.asset.stem : '') : ''}
        description={
          zoomed?.kind === 'file'
            ? `${formatOf(zoomed.panel)} · ${translate('measure.cmSize', { w: formatCm(zoomed.panel.native_w_mm), h: formatCm(zoomed.panel.native_h_mm) })}`
            : zoomed?.kind === 'runtime'
              ? `${ab('runtimeBadge')} · ${zoomed.asset.script}`
              : ''
        }
        size="lg"
        footer={
          <Button
            variant="primary"
            size="md"
            disabled={zoomed?.kind === 'runtime' && !zoomed.asset.descriptor}
            onClick={() => {
              // 文件与 runtime 同一条路：已经在文档里就只是聚焦它，绝不叠第二份
              if (zoomed && (zoomed.kind === 'file' || zoomed.asset.descriptor))
                addFigureToLayout(itemId(zoomed))
              setZoomed(null)
            }}
          >
            <Plus size={ICON_SIZE.md} />
            {ab('addToCanvas')}
          </Button>
        }
      >
        {/* 白弹窗里不再给图套一个框：白上白无需边（宪法第八节；左栏审计 L39） */}
        {zoomed?.kind === 'file' && (
          <div className="flex items-center justify-center bg-paper p-2">
            <img
              src={zoomed.panel.kind === 'svg' ? fileUrl(zoomed.panel.id, zoomed.panel.mtime) : renderUrl(zoomed.panel.id, 800, zoomed.panel.mtime)}
              alt={ab('zoomAlt', { name: fileName(zoomed.panel.id) })}
              className="max-h-[56vh] max-w-full object-contain"
            />
          </div>
        )}
        {zoomed?.kind === 'runtime' && <RuntimeZoom asset={zoomed.asset} />}
      </Dialog>
    </div>
  )
}

/**
 * 可收起的区标题（图 / 脚本共用一副）：分区小标题那一档（type-section），前面一个折叠
 * 箭头（xs，展开转 90°，与树 / 检查器同一套记号），后面可选一个计数——计数只是一个
 * type-meta 数字，不跟着标题一起加粗（宪法第九节；左栏审计 L03）。整行是按钮，热区 28px。
 */
function SectionToggle({
  label,
  count,
  open,
  onToggle,
  controls,
}: {
  label: string
  /** 「3」或筛选中的「3 / 7」；还没加载出来时不显示 */
  count?: ReactNode
  open: boolean
  onToggle: () => void
  controls: string
}) {
  return (
    <h3 className="px-2 pt-1">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={controls}
        onClick={onToggle}
        className={cn(
          'type-section flex h-7 w-full items-center gap-1 rounded-sm px-1 text-left outline-none',
          'transition-colors duration-fast hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
        )}
      >
        <ChevronRight
          size={ICON_SIZE.xs}
          aria-hidden
          className={cn('shrink-0 transition-transform duration-fast', open && 'rotate-90')}
        />
        {label}
        {/* `type-meta` 只管字号与颜色，字重会从按钮的 `type-section` 继承过来——
            计数得显式回到 400，才跟脚本组的「已关联 2」是同一种「名字 + meta 数字」 */}
        {count !== undefined && (
          <span className="type-meta ml-0.5 font-normal tabular-nums">{count}</span>
        )}
      </button>
    </h3>
  )
}

/** 来源 / 类型 / 排序 / 已使用收进同一个筛选 popover；目录路径在最底一行 */
function FilterButton({
  filters,
  folders,
  figuresDir,
  activeCount,
  onChange,
}: {
  filters: Filters
  folders: string[]
  /** 素材目录：排查用信息，放在弹层最底一行（此前是列表下一行常驻页脚，左栏审计 L04） */
  figuresDir: string | null
  activeCount: number
  onChange: (f: Filters) => void
}) {
  useTranslation('workspace')
  const patch = (p: Partial<Filters>) => onChange({ ...filters, ...p })
  return (
    <Popover
      width={224}
      align="end"
      trigger={
        <IconButton
          active={activeCount > 0}
          label={activeCount ? ab('filterActiveAria', { count: activeCount }) : ab('filterAria')}
        >
          <ListFilter size={ICON_SIZE.md} className={activeCount ? undefined : 'text-ink-2'} />
        </IconButton>
      }
    >
      <div className="flex flex-col gap-1.5">
        <Row label={ab('source')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.source}
            onChange={(source) => patch({ source })}
            ariaLabel={ab('sourceAria')}
            options={[
              { value: 'all', label: ab('sourceAll') },
              ...folders.map((f) => ({ value: f, label: folderLabel(f) })),
            ]}
          />
        </Row>
        <Row label={ab('type')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.type}
            onChange={(type) => patch({ type })}
            ariaLabel={ab('typeAria')}
            options={typeOptions()}
          />
        </Row>
        <Row label={ab('sort')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.sort}
            onChange={(sort) => patch({ sort })}
            ariaLabel={ab('sortAria')}
            options={sortOptions()}
          />
        </Row>
        <Row label={ab('usedOnly')} labelWidth={36}>
          <Toggle
            checked={filters.usedOnly}
            onChange={(usedOnly) => patch({ usedOnly })}
            aria-label={ab('usedOnlyAria')}
          />
        </Row>
        {activeCount > 0 && (
          <Button size="sm" className="self-end text-ink-2" onClick={() => onChange(DEFAULT_FILTERS)}>
            {ab('resetFilters')}
          </Button>
        )}
        {/* 路径是等宽字（宪法第六节）；按宽度从中间截，尾巴（真正区分目录的那一段）留着，
            完整路径在 title */}
        {figuresDir && (
          <p className="mt-0.5 font-mono type-meta" title={figuresDir}>
            <TruncateMiddle text={figuresDir} />
          </p>
        )}
      </div>
    </Popover>
  )
}

/** 骨架与真实卡片同尺寸（3:2 图片区 + 两行文字），加载完不跳版 */
function GridSkeleton({ columns }: { columns: number }) {
  return (
    <ul
      aria-hidden
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: 8 }, (_, i) => (
        // 骨架是静止的（宪法第七节：加载只有 sweep / 静态骨架 / shimmer / 转圈四种写法，不呼吸）
        <li key={i} className="overflow-hidden rounded-lg bg-surface opacity-65">
          <div className="aspect-[3/2] bg-surface-hover" />
          <div className="flex flex-col gap-1 px-1.5 py-1.5">
            <div className="h-3 rounded-xs bg-surface-hover" />
            <div className="h-3 w-3/5 rounded-xs bg-surface-hover" />
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * 一张素材卡：图片占绝大部分面积，识别靠图不靠文件名。
 *
 * **两个明确的动作，各说各的后果**（UI 审计 T06）：
 *
 * * **编辑原图**（Enter / 双击 / 就近按钮）——进快速编辑工作区。图还不在
 *   文档里时它会把图加进来（快速编辑的对象只能是文档里的面板对象，ADR 0028），
 *   而这一步由 `openFastEdit` 用一条状态提示说出口、一次撤销可移除；
 * * **添加到画布**（Shift+Enter / 就近按钮 / 拖拽 / 看大图弹窗）——文档变更，
 *   已经在文档里就只是聚焦它。
 *
 * 从前这两件事共用一个叫「打开」的动作：用户以为在看图，版本预览里却多了一个
 * 对象、问题面板多了一批问题。普通用户要做的事仍然是"改这张图"，所以编辑
 * 仍是主动作——但它不能再把加入文档这件事藏在一个中性的词后面。
 */
function AssetCard({
  panel,
  used,
  selected,
  tabbable,
  onSelect,
  onOpen,
  onAdd,
  onZoom,
  onMove,
  onMenu,
  columns,
}: {
  panel: PanelInfo
  used: number
  selected: boolean
  tabbable: boolean
  onSelect: () => void
  onOpen: () => void
  onAdd: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  /** 右键 / ⇧F10：在这个屏幕位置打开这张卡的菜单 */
  onMenu: (at: { x: number; y: number }) => void
  columns: number
}) {
  useTranslation('workspace')
  const name = fileName(panel.id)
  const cap = panel.capability
  const label = [
    name,
    formatOf(panel),
    // 状态进可达名。`capability` 缺席 = **这一轮还不知道**（就绪度扫描与素材
    // 遍历之间新出现的那一档），不是 `layout_only`——那时照旧只说老那句
    // 「可参数化」，绝不替后端补一个默认状态。
    cap ? statusLabel(cap.status) : panel.script ? ab('cardParameterizable') : null,
    // 需要说话的接入状态把原因也带进可达名（页脚里那段常驻说明去掉后，读屏的出处就在这里）
    cap && cap.status !== 'editable' ? reasonText(cap) : null,
    ab('cardSize', {
      w: formatCm(panel.native_w_mm),
      h: formatCm(panel.native_h_mm),
    }),
    used ? ab('cardUsed', { count: used }) : null,
  ]
    .filter(Boolean)
    .join('，')

  return (
    <li
      role="option"
      aria-selected={selected}
      aria-label={label}
      aria-keyshortcuts={CARD_KEYSHORTCUTS}
      tabIndex={tabbable ? 0 : -1}
      data-card={panel.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-panel-id', panel.id)
        e.dataTransfer.effectAllowed = 'copy'
      }}
      onClick={onSelect}
      onDoubleClick={onOpen}
      onFocus={onSelect}
      onContextMenu={(e) => {
        e.preventDefault()
        onSelect()
        onMenu({ x: e.clientX, y: e.clientY })
      }}
      onKeyDown={(e) => {
        const step: Record<string, number> = {
          ArrowRight: 1,
          ArrowLeft: -1,
          ArrowDown: columns,
          ArrowUp: -columns,
        }
        if (e.key === 'Enter') {
          e.preventDefault()
          // Enter = 编辑原图；Shift+Enter = 添加到画布（两个动作，两个键）
          if (e.shiftKey) onAdd()
          else onOpen()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
          e.preventDefault()
          const r = e.currentTarget.getBoundingClientRect()
          onMenu({ x: r.left + 16, y: r.top + 16 })
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('cardTitle', { id: panel.id })}
      className={cn(CARD_ITEM, 'cursor-grab active:cursor-grabbing')}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '140px' }}
    >
      <Card appearance="raised" padding="none" interactive selected={selected} className="overflow-hidden">
      <CardPreview>
        <img
          loading="lazy"
          src={panel.kind === 'svg' ? fileUrl(panel.id, panel.mtime) : renderUrl(panel.id, 400, panel.mtime)}
          alt=""
          draggable={false}
          className="h-full w-full object-contain p-1"
        />

        {/* 不是 <button>：option 里不许再嵌交互控件（axe nested-interactive，
            serious）——哪怕 tabIndex=-1 也算。这两个只是鼠标用户的就近入口；
            键盘 / 读屏用户在 option 上按 Enter / Shift+Enter 走同一对动作，
            列表下方 `SelectedAssetActions` 里还有一对真按钮。 */}
        <CardAdd icon={Plus} label={ab('addToCanvas')} title={ab('addAria', { name })} onClick={onAdd} />
      </CardPreview>

      {/* 文字区：文件名一行、元数据一行。格式 / 尺寸 / 接入状态 / 使用次数都在
          这里，预览上不再压任何标签——图就是图。
          接入状态**只在需要说话时说话**：`editable` 已经有「可编辑的图」那个紧凑
          角标（`ui/semanticIcons`），再写一遍「可编辑」是纯噪音；完整解释在 `title`
          与卡片外的说明条里。 */}
      <CardMeta
        name={name}
        selected={selected}
        marker={
          panel.script ? (
            <span
              role="img"
              aria-label={ab('scriptBadgeTitle')}
              className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3"
              title={ab('scriptBadgeTitle')}
            >
              <EditableFigureIcon size={ICON_SIZE.xs} />
            </span>
          ) : undefined
        }
        parts={[
          formatOf(panel),
          translate('measure.cmSize', {
            w: formatCm(panel.native_w_mm),
            h: formatCm(panel.native_h_mm),
          }),
          cap && cap.status !== 'editable'
            ? { text: statusLabel(cap.status), title: reasonText(cap) }
            : null,
        ]}
        used={used}
      />
      </Card>
    </li>
  )
}

/**
 * 一张运行时图卡（RuntimeFigureAsset，ADR 0013）：没有磁盘原件，预览来自
 * materialized cache。有描述符（跑过）→ Enter/双击编辑原图、Shift+Enter 添加到
 * 画布（与文件卡同一对动作）；还没跑过 → 主动作变成「运行并发现图」（尺寸与
 * 内容只有运行后才知道，绝不给假路径 / 假尺寸——负向反证 #2：这里要求磁盘
 * path 的话加入画布当场断）。
 *
 * `sibling` = 同一脚本、同一 stem 的磁盘图（`runtimeSiblingOf`）：有的话第二行
 * 写「同源：X.pdf」而不是脚本路径——两张卡并排时用户要的是关系，不是来源。
 */
function RuntimeAssetCard({
  asset,
  sibling,
  used,
  selected,
  tabbable,
  onSelect,
  onAdd,
  onZoom,
  onMove,
  onMenu,
  columns,
}: {
  asset: RuntimeAssetInfo
  sibling?: PanelInfo
  used: number
  selected: boolean
  tabbable: boolean
  onSelect: () => void
  onAdd: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  onMenu: (at: { x: number; y: number }) => void
  columns: number
}) {
  useTranslation('workspace')
  const nonce = useRuntimeAssetStore((s) => s.previewNonce[asset.id])
  const run = useScriptRunStore((s) => s.byScript[asset.script])
  const busy = !!run && isBusyPhase(run.phase)

  const primary = () => {
    // 跑过的运行时图与磁盘素材同一条路：编辑原图 → 快速编辑。还没跑过的那一档
    // 主动作仍然是"运行并发现图"——尺寸与内容只有运行后才知道。
    if (asset.descriptor) openFastEdit(asset.id)
    else if (!busy) void useScriptRunStore.getState().run(asset.script)
  }
  const add = () => {
    // 没有描述符就没有可添加的东西（尺寸未知）——Shift+Enter 落到这里时安静地不做
    if (asset.descriptor) onAdd()
  }
  const staleKey = runtimeStaleKey(asset)

  const label = [
    asset.stem,
    ab('runtimeBadge'),
    ab('runtimeFromScript', { script: asset.script }),
    asset.size_mm
      ? ab('cardSize', { w: formatCm(asset.size_mm[0]), h: formatCm(asset.size_mm[1]) })
      : ab('runtimeNeedsRun'),
    sibling ? ab('runtimeSiblingOf', { name: fileName(sibling.id) }) : null,
    staleKey ? translate(`panelBadge.${staleKey}`, { ns: 'workspace' }) : null,
    used ? ab('cardUsed', { count: used }) : null,
  ]
    .filter(Boolean)
    .join('，')

  return (
    <li
      role="option"
      aria-selected={selected}
      aria-label={label}
      aria-keyshortcuts={asset.descriptor ? CARD_KEYSHORTCUTS : 'Enter Space'}
      tabIndex={tabbable ? 0 : -1}
      data-card={asset.id}
      onClick={onSelect}
      onDoubleClick={primary}
      onFocus={onSelect}
      onContextMenu={(e) => {
        e.preventDefault()
        onSelect()
        onMenu({ x: e.clientX, y: e.clientY })
      }}
      onKeyDown={(e) => {
        const step: Record<string, number> = {
          ArrowRight: 1,
          ArrowLeft: -1,
          ArrowDown: columns,
          ArrowUp: -columns,
        }
        if (e.key === 'Enter') {
          e.preventDefault()
          if (e.shiftKey) add()
          else primary()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
          e.preventDefault()
          const r = e.currentTarget.getBoundingClientRect()
          onMenu({ x: r.left + 16, y: r.top + 16 })
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('runtimeCardTitle', { stem: asset.stem, script: asset.script })}
      className={CARD_ITEM}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '140px' }}
    >
      <Card appearance="raised" padding="none" interactive selected={selected} className="overflow-hidden">
      <CardPreview empty={!asset.cached}>
        {asset.cached ? (
          <img
            loading="lazy"
            src={runtimePreviewUrl(asset.id, nonce)}
            alt=""
            draggable={false}
            className="h-full w-full object-contain p-1"
          />
        ) : (
          <span className="flex flex-col items-center gap-1 p-2 text-center type-meta">
            <Play size={ICON_SIZE.md} className="text-ink-faint" />
            {ab('runtimeNeedsRun')}
          </span>
        )}

        {/* 就近入口（与文件卡同款，非嵌套控件）：有描述符 = 编辑原图 + 添加到
            画布；没有 = 只有「运行并发现图」 */}
        {asset.descriptor ? (
          <CardAdd icon={Plus} label={ab('addToCanvas')} title={ab('addAria', { name: asset.stem })} onClick={add} />
        ) : (
          <CardAdd
            icon={Play}
            label={translate(busy ? 'scripts.running' : 'scripts.run', { ns: 'workspace' })}
            title={ab('runtimeRunAria', { script: asset.script })}
            onClick={primary}
          />
        )}
      </CardPreview>

      {/* 第二行：「运行时图」是它的格式名；之后有同源磁盘图先说关系（脚本路径在
          title 与可达名里仍然有），否则尺寸（跑过）或脚本路径（没跑过） */}
      <CardMeta
        name={asset.stem}
        selected={selected}
        marker={
          <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3">
            <Zap size={ICON_SIZE.xs} />
          </span>
        }
        parts={[
          ab('runtimeBadge'),
          sibling
            ? ab('runtimeSiblingOf', { name: fileName(sibling.id) })
            : asset.size_mm
              ? translate('measure.cmSize', {
                  w: formatCm(asset.size_mm[0]),
                  h: formatCm(asset.size_mm[1]),
                })
              : { text: asset.script, title: asset.script },
        ]}
        used={used}
        // 只留状态文字：「重新运行」是真按钮，住在列表下方的 `SelectedAssetActions`——
        // option 里嵌一颗可 Tab 的按钮是 axe 的 nested-interactive（此前就嵌在这里）
        extra={
          staleKey ? (
            // 文字 ink-3，只有 14px 图标染色（UI 2.0：颜色只在图标上）
            <p data-runtime-stale className="flex items-center gap-1 truncate text-xs text-ink-3">
              <TriangleAlert size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-danger" />
              <span className="truncate">{translate(`panelBadge.${staleKey}`, { ns: 'workspace' })}</span>
            </p>
          ) : undefined
        }
      />
      </Card>
    </li>
  )
}

/**
 * runtime 卡的 stale 角标 key（复用 panelBadge.runtime*，画布角标同一批 key）；null = 不提示。
 * 还没跑过的卡片占位区已经写着「尚未运行」，needs_rerun 不再重复一行。卡片的状态文字与
 * 选中页脚的「重新运行」按钮认同一个判据。
 */
function runtimeStaleKey(asset: RuntimeAssetInfo): string | null {
  return asset.status === 'fresh' || (!asset.cached && asset.status === 'needs_rerun')
    ? null
    : `runtime${asset.status
        .split('_')
        .map((s) => s[0].toUpperCase() + s.slice(1))
        .join('')}`
}

/**
 * 卡片外壳（2026-10-07 设计审计 §5 / §10.3）：`ui/Card` raised + interactive——hover 画 1px outline、
 * 选中 = border-strong outline + selected 底（选中的名字再加一档字重），几何永不变化；圆角 lg 12。
 * `li` 只是 option 本身（焦点、键盘、拖拽在它上面），焦点环按卡片的圆角画在它外面。
 */
const CARD_ITEM = 'group relative rounded-lg outline-none focus-visible:focus-ring'

/**
 * 预览区：3:2、白底、内容按比例缩放；上面只有悬停时的就近入口，没有常驻标签。
 * 底是纸（图本身的白，两套主题同值）；还没有图可画（运行时图没跑过）时里面是界面的说明字，不是图——
 * 底换成界面的 surface-2（与行内缩略格「没图 = surface-2」同一手法），字不落在暗色里也不变的白纸上。
 */
function CardPreview({ children, empty = false }: { children: ReactNode; empty?: boolean }) {
  return (
    <div
      className={cn(
        'relative flex aspect-[3/2] items-center justify-center overflow-hidden',
        empty ? 'bg-surface-2' : 'bg-paper',
      )}
    >
      {children}
    </div>
  )
}

/** 元数据行里的一段：纯文字，或带 title（悬停看完整解释）的文字 */
type MetaPart = string | { text: string; title?: string } | null | undefined

/**
 * 卡片文字区：第一行名字（+ 一个 16px 的标记位），第二行元数据用「·」串起来、
 * 使用次数靠右。两行都是单行截断，长文件名 / 长状态不会把卡片撑高。
 * 名字是正文档（12），元数据 11：主文字与 meta 之间要有 2px 的台阶，不能只靠颜色分层
 * （宪法第六节；左栏审计 L02）。
 */
function CardMeta({
  name,
  selected,
  marker,
  parts,
  used,
  extra,
}: {
  name: string
  selected: boolean
  marker?: ReactNode
  parts: MetaPart[]
  used: number
  extra?: ReactNode
}) {
  const shown = parts.filter((p): p is Exclude<MetaPart, null | undefined> => p != null)
  return (
    <div className="flex flex-col px-1.5 py-1">
      <p className="flex items-center gap-1 text-sm">
        <span className={cn('min-w-0 truncate', selected ? 'font-medium text-ink' : 'text-ink')} title={name}>
          {name}
        </span>
        {marker}
      </p>
      <p className={cn('flex items-center gap-1 type-meta tabular-nums', selected && 'text-ink-2')}>
        {shown.map((part, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <span aria-hidden className="shrink-0">
                ·
              </span>
            )}
            <span
              className={cn('truncate', i === 0 ? 'shrink-0' : 'min-w-0')}
              title={typeof part === 'string' ? undefined : part.title}
            >
              {typeof part === 'string' ? part : part.text}
            </span>
          </Fragment>
        ))}
        {used > 0 && (
          <span className="ml-auto shrink-0 pl-1" title={ab('cardUsed', { count: used })}>
            ×{used}
          </span>
        )}
      </p>
      {extra}
    </div>
  )
}

/** 卡片可达名旁的键位说明（aria-keyshortcuts 的语法：空格分隔的组合键） */
const CARD_KEYSHORTCUTS = 'Enter Shift+Enter Space'

/**
 * 卡片唯一的就近入口（2026-10-16，UI 2.0 §08）：缩略图**右上角**一枚 24px 的圆「＋ 添加到画布」，悬停 / 键盘焦点
 * 落在卡上才出现——不压图里的文字（此前在右下角，正好盖住图例与坐标轴）。编辑原图 = 双击卡片 / Enter；
 * 其余动作在右键 / ⇧F10 菜单里。
 * 不是 <button>——option 里不许再嵌交互控件（axe nested-interactive），键盘走 Shift+Enter。
 * 外观是分段选择器那块白色 thumb 的做法（`shadow-thumb`），不用浮层的大模糊（左栏审计 L09）。
 */
function CardAdd({
  icon: Icon,
  label,
  title,
  onClick,
}: {
  icon: IconComponent
  label: string
  title: string
  onClick: () => void
}) {
  return (
    <span
      data-card-add
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      // 光标一律箭头（宪法第二十六节）：卡片本身可拖（抓手），就近入口不是拖拽把手
      className={cn(
        'absolute right-1.5 top-1.5 flex h-6 w-6 cursor-default select-none items-center justify-center rounded-full',
        'bg-thumb text-ink opacity-0 shadow-thumb transition-[opacity,background-color] duration-fast',
        'hover:bg-surface-2 group-hover:opacity-100 group-focus-visible:opacity-100',
      )}
    >
      <Icon size={ICON_SIZE.sm} />
      <span className="sr-only">{label}</span>
    </span>
  )
}

/**
 * 是素材、但用不了的文件（支持范围之外的 TIFF，issue #534）：不给卡片（没有尺寸、画不出来），
 * 也不静默消失——每个文件一行，说清为什么、该怎么另存。原因由后端的 code 定，文案在
 * `errors:backend.<code>`（与 422 / 导出失败同一句话）。
 */
function UnsupportedAssets({ items }: { items: UnsupportedAsset[] }) {
  useTranslation('workspace')
  return (
    <div className="mt-2" data-asset-unsupported>
      <p className="type-meta text-ink-3">{ab('unsupportedTitle')}</p>
      <ul className="mt-1 space-y-1">
        {items.map((u) => (
          <li key={u.id} className="flex gap-1.5 text-xs text-ink-2" title={u.id}>
            <TriangleAlert size={ICON_SIZE.sm} className="mt-0.5 shrink-0 text-danger" aria-hidden />
            <span className="min-w-0">
              {formatMessage(backendCodeMsg(u.code, { file: u.name }, u.name))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 一张卡的右键 / ⇧F10 菜单：这张卡的**全部**动作（2026-10-16 取代原来的选中页脚）。
 * 编辑原图 / 添加到画布；跑过但可能过期的运行时图多一项「重新运行」，没跑过的只有「运行并发现图」
 * （没有描述符就没有能编辑、能添加的东西）；接入状态需要说话的图多一项「查看接入状态」，原因写在项的第二行。
 * 菜单在 listbox 之外：option 里不许再嵌可 Tab 的控件。
 */
function AssetCardMenu({
  item,
  panel,
  at,
  onClose,
}: {
  item: LibraryItem | undefined
  panel?: PanelInfo
  at: { x: number; y: number }
  onClose: () => void
}) {
  useTranslation('workspace')
  const run = useScriptRunStore((s) =>
    item?.kind === 'runtime' ? s.byScript[item.asset.script] : undefined,
  )
  if (!item) return null
  const name = item.kind === 'file' ? fileName(item.panel.id) : item.asset.stem
  const actionable = item.kind === 'file' || !!item.asset.descriptor
  const busy = !!run && isBusyPhase(run.phase)
  const stale = item.kind === 'runtime' && actionable && !!runtimeStaleKey(item.asset)
  const runScript = () => {
    if (item.kind === 'runtime' && !busy) void useScriptRunStore.getState().run(item.asset.script)
  }
  const cap = panel?.capability
  const showCap = !!panel && !!cap && cap.status !== 'editable'
  return (
    <PointMenu
      open
      onOpenChange={(o) => !o && onClose()}
      at={at}
      width={220}
      ariaLabel={ab('selectedActionsAria', { name })}
      data-asset-card-menu=""
    >
      {actionable ? (
        <>
          <MenuItem icon={Pencil} onSelect={() => void openFastEdit(itemId(item))}>
            {ab('openFigure')}
          </MenuItem>
          <MenuItem icon={Plus} onSelect={() => void addFigureToLayout(itemId(item))}>
            {ab('addToCanvas')}
          </MenuItem>
          {stale && (
            <MenuItem icon={RefreshCw} disabled={busy} data-asset-rerun onSelect={runScript}>
              {translate(busy ? 'scripts.running' : 'scripts.rerun', { ns: 'workspace' })}
            </MenuItem>
          )}
        </>
      ) : (
        <MenuItem icon={Play} disabled={busy} onSelect={runScript}>
          {translate(busy ? 'scripts.running' : 'scripts.run', { ns: 'workspace' })}
        </MenuItem>
      )}
      {showCap && (
        <MenuItem
          icon={Info}
          data-asset-capability
          reason={reasonText(cap)}
          onSelect={() => useProjectReadinessStore.getState().focusPanel(panel.id, 'panel')}
        >
          {translate('readiness.openCenter', { ns: 'workspace' })}
        </MenuItem>
      )}
    </PointMenu>
  )
}

/** 运行时图的大图预览：cache 预览 + 「没有原始文件」的如实说明 */
function RuntimeZoom({ asset }: { asset: RuntimeAssetInfo }) {
  useTranslation('workspace')
  const nonce = useRuntimeAssetStore((s) => s.previewNonce[asset.id])
  return (
    <div className="flex flex-col gap-2">
      {/* 白弹窗里不再给图套框（左栏审计 L39）；占位块只留一层浅底 */}
      {asset.cached ? (
        <div className="flex items-center justify-center bg-paper p-2">
          <img
            src={runtimePreviewUrl(asset.id, nonce)}
            alt={ab('zoomAlt', { name: asset.stem })}
            className="max-h-[50vh] max-w-full object-contain"
          />
        </div>
      ) : (
        <p className="rounded-sm bg-surface-2 p-3 text-center text-xs text-ink-3">
          {ab('runtimeNeedsRun')}
        </p>
      )}
      <p className="text-xs leading-relaxed text-ink-3">{ab('runtimeNoFile')}</p>
    </div>
  )
}
