import { requestRender } from '@/hooks/useEngineSync'
import { newId } from '@/lib/id'
import { applyAlign, boundsOf, readingOrder, type AlignMode } from '@/lib/geometry'
import { clamp } from '@/lib/units'
import { modKey } from '@/lib/utils'
import type { PanelInfo } from '@/lib/api'
import type { StylePlan, StylePreset } from '@/lib/stylePresets'
import { reflowPatches, sizeSignature } from '@/lib/layoutGroups'
import type {
  ArrowObject,
  CanvasObject,
  FigureDocument,
  LayoutGroup,
  PanelObject,
  ShapeObject,
  TextObject,
} from '@/types/document'
import { emptyProject, objectLabel, type ProjectDocument } from '@/types/document'
import { useAssetStore } from './assetStore'
import { readAutosaveDoc, useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { askConfirm, useUiStore } from './uiStore'
import { useViewportStore } from './viewportStore'
import { rectOf, type Rect } from '@/lib/geometry'
import type { CropRect, PanelRotation } from '@/types/document'
import {
  panelAspectLocked,
  panelContentSize,
  panelRotation,
  rotateVec,
  rotationSwaps,
} from '@/types/document'

const doc = () => useDocumentStore.getState().doc
const commit = (label: string, recipe: (d: FigureDocument) => void) =>
  useDocumentStore.getState().commit(label, recipe)
const select = (ids: string[]) => useSelectionStore.getState().set(ids)
const status = (msg: string, tone?: 'info' | 'error') =>
  useUiStore.getState().setStatus(msg, tone)

export const findObject = (id: string): CanvasObject | undefined =>
  doc().objects.find((o) => o.id === id)

export const selectedObjects = (): CanvasObject[] => {
  const ids = useSelectionStore.getState().ids
  return doc().objects.filter((o) => ids.includes(o.id))
}

/* ------------------------------- 新增对象 --------------------------------- */

export function addPanel(info: PanelInfo, atX?: number, atY?: number) {
  const page = doc().page
  // 按原始尺寸放入（100% 缩放）：等效字号即原字号，所见即出版效果；
  // 比页面宽也不自动缩小，要多大用户自己定
  const w = info.native_w_mm
  const h = info.native_h_mm
  const obj: PanelObject = {
    id: newId('p'),
    type: 'panel',
    fileId: info.id,
    fileKind: info.kind,
    nativeW: info.native_w_mm,
    nativeH: info.native_h_mm,
    pxW: info.px_w,
    script: info.script ?? null,
    cost: info.cost,
    // 继承「写回原始文件」的基线，这样编辑态看到的就是文件当前的样子
    overrides: info.baked_overrides ? structuredClone(info.baked_overrides) : [],
    name: info.name,
    x: clamp(atX != null ? atX - w / 2 : (page.w - w) / 2, -w * 0.9, page.w - w * 0.1),
    y: clamp(atY != null ? atY - h / 2 : (page.h - h) / 2, -h * 0.9, page.h - h * 0.1),
    w,
    h,
  }
  commit(`加入面板 ${info.name}`, (d) => {
    d.objects.push(obj)
  })
  select([obj.id])
  useAssetStore.getState().markUsed(info.id)
  return obj
}

export function addText(partial: Partial<TextObject> = {}) {
  const page = doc().page
  const obj: TextObject = {
    id: newId('t'),
    type: 'text',
    text: '文字',
    x: page.w / 2 - 20,
    y: page.h / 2 - 4,
    w: 40,
    h: 5,
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    ...partial,
  }
  commit('添加文字', (d) => {
    d.objects.push(obj)
  })
  select([obj.id])
  return obj
}

export function addArrow(partial: Partial<ArrowObject> = {}) {
  const page = doc().page
  const obj: ArrowObject = {
    id: newId('a'),
    type: 'arrow',
    x: page.w / 2 - 15,
    y: page.h / 2 - 5,
    w: 30,
    h: 10,
    start: { rx: 0, ry: 1 },
    end: { rx: 1, ry: 0 },
    strokePt: 1,
    color: '#1B1B18',
    head: 'end',
    ...partial,
  }
  commit('添加箭头', (d) => {
    d.objects.push(obj)
  })
  select([obj.id])
  return obj
}

export function addShape(shape: ShapeObject['shape'], partial: Partial<ShapeObject> = {}) {
  const page = doc().page
  const obj: ShapeObject = {
    id: newId('s'),
    type: 'shape',
    shape,
    x: page.w / 2 - 15,
    y: page.h / 2 - 10,
    w: 30,
    h: 20,
    strokePt: 1,
    color: '#1B1B18',
    fill: null,
    ...partial,
  }
  commit(`添加${{
    rect: '矩形', ellipse: '椭圆', line: '直线', triangle: '三角形',
    diamond: '菱形', polygon: '多边形', brace: '大括号',
  }[shape]}`, (d) => {
    d.objects.push(obj)
  })
  select([obj.id])
  return obj
}

/** 按阅读顺序给所有面板添加 (a)(b)(c) 标签 */
export function addSubLabels() {
  const panels = readingOrder(doc().objects.filter((o) => o.type === 'panel'))
  if (!panels.length) {
    status('画布上还没有面板')
    return
  }
  const created: string[] = []
  commit('添加序号标签', (d) => {
    panels.forEach((p, i) => {
      const label: TextObject = {
        id: newId('t'),
        type: 'text',
        text: `(${String.fromCharCode(97 + i)})`,
        x: p.x + 1.5,
        y: p.y + 1,
        w: 10,
        h: 5,
        sizePt: 10,
        bold: true,
        color: '#000000',
        align: 'left',
      }
      created.push(label.id)
      d.objects.push(label)
    })
  })
  select(created)
  status(`已添加 ${panels.length} 个序号标签（按从上到下、从左到右排序）`)
}

/* ------------------------------- 编辑操作 --------------------------------- */

export function updateObject<T extends CanvasObject>(id: string, label: string, patch: (o: T) => void) {
  commit(label, (d) => {
    const o = d.objects.find((x) => x.id === id) as T | undefined
    if (o) patch(o)
  })
}

export function updateObjects(ids: string[], label: string, patch: (o: CanvasObject) => void) {
  commit(label, (d) => {
    for (const o of d.objects) if (ids.includes(o.id)) patch(o)
  })
}

export function deleteSelected() {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  const names = ids.length === 1 ? objectLabel(findObject(ids[0])!) : `${ids.length} 个对象`
  commit(`删除 ${names}`, (d) => {
    d.objects = d.objects.filter((o) => !ids.includes(o.id))
    // 成员不足 2 个的布局组失去意义，一并清掉（约束消失，剩余对象原地不动）
    if (d.layoutGroups?.length) {
      d.layoutGroups = d.layoutGroups.filter(
        (g) => d.objects.filter((o) => o.groupId === g.id).length >= 2,
      )
    }
  })
  useSelectionStore.getState().clear()
}

export function duplicateSelected() {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  // 克隆**必须**在 commit 的 recipe 外面做：recipe 里的 d 是 Immer 草稿，
  // d.objects.filter(...) 逐个取到的是元素的草稿 Proxy，而 structuredClone
  // 对任何 Proxy 都直接抛 DataCloneError（引擎级行为）——复制会 100% 静默失败。
  // 对已 finalize 的 plain object 克隆好再整体 push，与 clipboard.materializePaste 同一模式。
  const regroup = new Map<string, string>()
  const clones: CanvasObject[] = doc()
    .objects.filter((o) => ids.includes(o.id))
    .map((o) => {
      const copy = structuredClone(o)
      copy.id = newId(o.type[0])
      copy.x += 4
      copy.y += 4
      // 组要跟着复制成新的一份，否则副本会和原件粘成同一组
      if (copy.groupId) {
        const next = regroup.get(copy.groupId) ?? newId('g')
        regroup.set(copy.groupId, next)
        copy.groupId = next
      }
      return copy
    })
  if (!clones.length) return
  commit('复制对象', (d) => {
    d.objects.push(...clones)
  })
  select(clones.map((c) => c.id))
}

export type ZMove = 'top' | 'bottom' | 'up' | 'down'

export function changeZOrder(move: ZMove) {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  const labels: Record<ZMove, string> = {
    top: '置于顶层',
    bottom: '置于底层',
    up: '上移一层',
    down: '下移一层',
  }
  commit(labels[move], (d) => {
    // 从后往前处理，避免同批移动时互相挤压
    const picked = d.objects.filter((o) => ids.includes(o.id))
    const rest = d.objects.filter((o) => !ids.includes(o.id))
    if (move === 'top') {
      d.objects = [...rest, ...picked]
      return
    }
    if (move === 'bottom') {
      d.objects = [...picked, ...rest]
      return
    }
    const step = move === 'up' ? 1 : -1
    const order = move === 'up' ? [...d.objects].reverse() : d.objects
    const next = [...d.objects]
    for (const o of order) {
      if (!ids.includes(o.id)) continue
      const i = next.indexOf(o)
      const j = i + step
      if (j < 0 || j >= next.length || ids.includes(next[j].id)) continue
      next.splice(i, 1)
      next.splice(j, 0, o)
    }
    d.objects = next
  })
}

export function alignSelected(mode: AlignMode) {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  if ((mode === 'hdist' || mode === 'vdist') && ids.length < 3) {
    status('等距分布需要至少选中 3 个对象')
    return
  }
  const primaryId = ids.at(-1)!
  const labels: Record<AlignMode, string> = {
    left: '左对齐',
    hcenter: '水平居中',
    right: '右对齐',
    top: '顶对齐',
    vcenter: '垂直居中',
    bottom: '底对齐',
    hdist: '水平等距',
    vdist: '垂直等距',
    samew: '等宽',
    sameh: '等高',
  }
  commit(labels[mode], (d) => {
    const objs = d.objects.filter((o) => ids.includes(o.id))
    const primary = objs.find((o) => o.id === primaryId)
    applyAlign(objs, mode, d.page, primary)
  })
}

export function nudgeSelected(dx: number, dy: number) {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  // 与鼠标拖动同一套规则：组内有锁定成员就整组不动（movableTargets）
  const { objects, blockedGroups } = movableTargets(ids)
  warnBlockedGroups(blockedGroups, objects.length > 0)
  if (!objects.length) return
  const moving = new Set(objects.map((o) => o.id))
  commit('移动对象', (d) => {
    for (const o of d.objects) {
      if (!moving.has(o.id)) continue
      o.x += dx
      o.y += dy
    }
  })
}

/** 选中这些对象并把它们挪进视野——导出预检的警告点击后用 */
export function revealObjects(ids: string[]) {
  const objs = doc().objects.filter((o) => ids.includes(o.id))
  if (!objs.length) return
  select(objs.map((o) => o.id))
  const bounds = boundsOf(objs)
  if (bounds) useViewportStore.getState().revealRect(bounds)
}

export function selectAll() {
  select(doc().objects.filter((o) => !o.hidden && !o.locked).map((o) => o.id))
}

/* ------------------------------- 文档切换 --------------------------------- */

/**
 * 快照失败时才会走到的确认——正常路径永远静默。
 * 文案要说清影响范围：不是「可能丢失」，而是「切走后取不回来」。
 */
const confirmLoss = () =>
  askConfirm({
    title: '当前文档无法保存到本机',
    body: '浏览器存储不可用或已写满，切换后将无法从「最近文档」取回当前文档。仍要继续吗？',
    confirmLabel: '仍要切换',
    danger: true,
  })

/** 切换文档后视口重新适配，选中态清空——三个入口共用（切换成功后再调） */
function afterSwitch(): void {
  useSelectionStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
  const page = useDocumentStore.getState().doc.page
  useViewportStore.getState().fit(page.w, page.h)
}

export async function newBlankDocument(): Promise<void> {
  const next = emptyProject()
  if (!(await useDocumentStore.getState().switchDocument(next, newId('d'), confirmLoss))) return
  afterSwitch()
  status('已新建空白文档，原文档可从「最近文档」取回')
}

/** 载入画布文件：每次载入都是一个新的编辑会话，因此给一个新的文档身份 */
export async function openLayoutDocument(doc: FigureDocument | ProjectDocument): Promise<void> {
  if (!(await useDocumentStore.getState().switchDocument(doc, newId('d'), confirmLoss))) return
  afterSwitch()
  const s = useDocumentStore.getState()
  status(`已载入：${s.projectMeta.name}（${s.canvases.length} 张画布）`)
}

/** 切回本机自动保存过的文档；沿用它原来的身份，槽位因此不会分叉 */
export async function openRecentDocument(id: string): Promise<void> {
  const pd = await readAutosaveDoc(id)
  if (!pd) {
    status('该文档的本机副本已不存在', 'error')
    return
  }
  if (!(await useDocumentStore.getState().switchDocument(pd, id, confirmLoss))) return
  afterSwitch()
  status(`已切回：${pd.project.name}（${pd.canvases.length} 张画布）`)
}

/* --------------------------------- 页面 ----------------------------------- */

export function setPageSize(w: number, h: number) {
  commit('修改画布尺寸', (d) => {
    d.page.w = clamp(w, 10, 1000)
    d.page.h = clamp(h, 10, 1000)
  })
}

/** 页面的非尺寸设置（背景、页边距）；尺寸走 setPageSize */
export function setPageSetup(patch: Partial<FigureDocument['page']>, label: string) {
  commit(label, (d) => {
    Object.assign(d.page, patch)
  })
}

/** 顶栏的「文档名」= 项目文档名；画布名走 renameCanvas（C2 画布管理） */
export function setDocumentName(name: string) {
  useDocumentStore.getState().renameProject(name)
}

/* -------------------------------- 参考线 ---------------------------------- */

export function addGuide(axis: 'x' | 'y', pos: number) {
  commit('添加参考线', (d) => {
    d.guides.push({ axis, pos })
  })
}

export function moveGuide(index: number, pos: number) {
  commit('移动参考线', (d) => {
    if (d.guides[index]) d.guides[index].pos = pos
  })
}

export function removeGuide(index: number) {
  commit('删除参考线', (d) => {
    d.guides.splice(index, 1)
  })
}

export function clearGuides() {
  commit('清除参考线', (d) => {
    d.guides = []
  })
}

/* -------------------------------- 图层 ------------------------------------ */

/**
 * 图层树拖放：把 fromId 放到 targetId 的上方或下方。
 * 「上方」= 视觉上更靠前 = 数组中更靠后。
 */
export function reorderObject(fromId: string, targetId: string, position: 'above' | 'below') {
  if (fromId === targetId) return
  commit('调整图层顺序', (d) => {
    const from = d.objects.find((o) => o.id === fromId)
    if (!from) return
    const rest = d.objects.filter((o) => o.id !== fromId)
    const at = rest.findIndex((o) => o.id === targetId)
    if (at < 0) return
    rest.splice(position === 'above' ? at + 1 : at, 0, from)
    d.objects = rest
  })
}

export function toggleHidden(id: string) {
  const o = findObject(id)
  if (!o) return
  updateObject(id, o.hidden ? '显示对象' : '隐藏对象', (obj) => {
    obj.hidden = !obj.hidden
  })
}

export function toggleLocked(id: string) {
  const o = findObject(id)
  if (!o) return
  updateObject(id, o.locked ? '解锁对象' : '锁定对象', (obj) => {
    obj.locked = !obj.locked
  })
}

export function renameObject(id: string, name: string) {
  updateObject(id, '重命名对象', (o) => {
    o.name = name.trim() || undefined
  })
}

/** 选区包围盒，状态栏与 Inspector 多选态用 */
export const selectionBounds = () => boundsOf(selectedObjects())

/* ---------------------------- 图内元素 override ---------------------------- */

/** manifest 里 prop 的中文名；未知属性原样显示，前端不硬编码 matplotlib 属性表 */
export const PROP_LABEL: Record<string, string> = {
  text: '内容',
  fontsize: '字号',
  color: '颜色',
  weight: '字重',
  style: '字形',
  visible: '显示',
  linewidth: '线宽',
  linestyle: '线型',
  markersize: '点径',
  xlim: 'X 范围',
  ylim: 'Y 范围',
  position: '子图占比',
  labelpad: '与轴距离',
  rotation: '旋转',
  frameon: '边框',
  size_mm: '图幅',
  pos_frac: '位置',
  loc_frac: '位置',
  alpha: '透明度',
  fontfamily: '字体',
  ha: '水平对齐',
  va: '垂直对齐',
  linespacing: '行距',
  zorder: '堆叠层级',
  bbox_visible: '背景',
  bbox_facecolor: '背景色',
  bbox_alpha: '背景透明度',
  bbox_edgecolor: '边框色',
  bbox_linewidth: '边框粗细',
  bbox_pad: '内边距',
  bbox_rounded: '圆角',
  stroke_enabled: '描边',
  stroke_color: '描边色',
  stroke_width: '描边宽度',
}

export const propLabel = (prop: string) => PROP_LABEL[prop] ?? prop

/**
 * enum 选项的中文名，按属性分档——"center" 在水平/垂直对齐里含义不同，
 * "normal" 在字重/字形里也不同，所以不能只按值查表。
 * 查不到的原样显示：具体字体名（Times New Roman 等）不该被翻译。
 */
const ENUM_LABEL: Record<string, Record<string, string>> = {
  ha: { left: '左', center: '中', right: '右' },
  va: { top: '上', center: '中', bottom: '下', baseline: '基线' },
  fontfamily: { serif: '衬线', 'sans-serif': '无衬线', monospace: '等宽' },
}

export const optionLabel = (prop: string, value: string) => ENUM_LABEL[prop]?.[value] ?? value

/**
 * 写入一条图内元素 override 并触发重渲染。
 * override 存在 PanelObject 上，因此天然进 undo history；
 * immediate=true 用于颜色/开关/枚举/拖动结束这类不需要防抖的改动。
 */
export function setOverride(
  panelId: string,
  gid: string,
  prop: string,
  value: unknown,
  immediate = false,
) {
  updateObject<PanelObject>(panelId, `修改${propLabel(prop)}`, (o) => {
    o.overrides = o.overrides.filter((p) => !(p.gid === gid && p.prop === prop))
    o.overrides.push({ gid, prop, value })
  })
  const panel = findObject(panelId)
  if (panel?.type === 'panel') requestRender(panel.fileId, panel.overrides, immediate)
}

/**
 * 「删除」图内元素 = 写 visible:false override。
 * 非破坏、进撤销、导出与写回原始文件都生效，随时可从「已隐藏元素」恢复。
 */
export function hideElement(panelId: string, gid: string, label: string) {
  setOverride(panelId, gid, 'visible', false, true)
  status(`已隐藏「${label}」，可在「已隐藏元素」里恢复`)
}

/** 多选一起隐藏：一次撤销、一次渲染（键盘 Delete 走这条） */
export function hideElements(panelId: string, targets: { gid: string; label: string }[]) {
  if (!targets.length) return
  if (targets.length === 1) {
    hideElement(panelId, targets[0].gid, targets[0].label)
    return
  }
  setOverrides(
    panelId,
    `隐藏 ${targets.length} 个图内元素`,
    targets.map((t) => ({ gid: t.gid, prop: 'visible', value: false })),
  )
  status(`已隐藏 ${targets.length} 个元素，可在「已隐藏元素」里恢复`)
}

/** 锁定/解锁图内元素：命中测试跳过锁定元素，元素树是唯一的解锁入口 */
export function toggleElementLocked(panelId: string, gid: string, label?: string) {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel') return
  const locked = panel.lockedGids?.includes(gid) ?? false
  updateObject<PanelObject>(panelId, locked ? '解锁图内元素' : '锁定图内元素', (o) => {
    const cur = o.lockedGids ?? []
    const next = locked ? cur.filter((g) => g !== gid) : [...cur, gid]
    o.lockedGids = next.length ? next : undefined
  })
  if (!locked) {
    // 锁定的元素不该继续是选中目标（属性页会继续改它）
    const ui = useUiStore.getState()
    if (ui.selectedGids.includes(gid)) {
      ui.setSelectedGid(null)
    }
    status(`已锁定「${label ?? gid}」，画布点击将跳过它`)
  }
}

/** 恢复：移除该元素的 visible override，而不是写 visible:true */
export function unhideElement(panelId: string, gid: string) {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel') return
  updateObject<PanelObject>(panelId, '恢复隐藏元素', (o) => {
    o.overrides = o.overrides.filter((p) => !(p.gid === gid && p.prop === 'visible'))
  })
  const next = findObject(panelId)
  if (next?.type === 'panel') requestRender(next.fileId, next.overrides, true)
}

/**
 * 清掉单个 prop 的 override —— 「自动范围」「恢复脚本原始尺寸」这类动作
 * 本质就是移除覆盖、让引擎回到脚本自己算的值，不是写一个新值。
 */
export function clearOverride(panelId: string, gid: string, prop: string) {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel') return
  if (!panel.overrides.some((p) => p.gid === gid && p.prop === prop)) return
  updateObject<PanelObject>(panelId, `恢复${propLabel(prop)}`, (o) => {
    o.overrides = o.overrides.filter((p) => !(p.gid === gid && p.prop === prop))
  })
  const next = findObject(panelId)
  if (next?.type === 'panel') requestRender(next.fileId, next.overrides, true)
}

/**
 * 批量移除 override：多选同类元素时「回到脚本值」要一次撤销、一次渲染，
 * 逐个调 clearOverride 会留下一串撤销记录。
 */
export function clearOverrides(
  panelId: string,
  label: string,
  targets: { gid: string; prop: string }[],
) {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel') return
  const hit = targets.filter((t) =>
    panel.overrides.some((p) => p.gid === t.gid && p.prop === t.prop),
  )
  if (!hit.length) return
  updateObject<PanelObject>(panelId, label, (o) => {
    o.overrides = o.overrides.filter(
      (p) => !hit.some((t) => t.gid === p.gid && t.prop === p.prop),
    )
  })
  const next = findObject(panelId)
  if (next?.type === 'panel') requestRender(next.fileId, next.overrides, true)
}

export function resetOverrides(panelId: string) {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel' || !panel.overrides.length) return
  updateObject<PanelObject>(panelId, '重置图内修改', (o) => {
    o.overrides = []
  })
  requestRender(panel.fileId, [], true)
  status('已清空该面板的图内修改')
}

/** 一次写入多条 override（子图对齐等批量操作）：一条历史、一次渲染 */
export function setOverrides(
  panelId: string,
  label: string,
  patches: { gid: string; prop: string; value: unknown }[],
) {
  if (!patches.length) return
  updateObject<PanelObject>(panelId, label, (o) => {
    for (const p of patches) {
      o.overrides = o.overrides.filter((x) => !(x.gid === p.gid && x.prop === p.prop))
      o.overrides.push(p)
    }
  })
  const panel = findObject(panelId)
  if (panel?.type === 'panel') requestRender(panel.fileId, panel.overrides, true)
}

/* ------------------------ 「写回原始文件」基线的继承 --------------------------- */

/** 该面板的 overrides 是否恰好等于资产基线（即文件上已经烙好、没再动过） */
export function isJustBakedBaseline(panel: PanelObject): boolean {
  const baked = useAssetStore.getState().byId[panel.fileId]?.baked_overrides
  if (!baked?.length) return false
  return JSON.stringify(panel.overrides) === JSON.stringify(baked)
}

/**
 * 给还没有任何 override 的老面板补播种基线。
 * 已经有自己修改的面板不碰——那是用户的编辑，不能被基线覆盖。
 */
export function seedBakedOverrides(panelId: string): number {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel' || panel.overrides.length) return 0
  const baked = useAssetStore.getState().byId[panel.fileId]?.baked_overrides
  if (!baked?.length) return 0
  updateObject<PanelObject>(panelId, '载入写回文件的基线', (o) => {
    o.overrides = structuredClone(baked)
  })
  return baked.length
}

/** 进入图内编辑的统一入口：先补基线再进编辑态，避免双击回到脚本原始状态 */
export function enterElementEdit(panelId: string) {
  const seeded = seedBakedOverrides(panelId)
  const ui = useUiStore.getState()
  ui.setElementPanel(panelId)
  // 三栏布局下左栏顺手切到元素树；窄断点不动（左右互斥，抢掉属性页得不偿失）
  if (ui.layout === 'wide' && ui.leftOpen && ui.leftTab !== 'elements') {
    ui.setLeftTab('elements')
  }
  if (seeded) status(`已载入写回文件时的基线（${seeded} 项）`)
}

/* ------------------------------ 论文样式应用 -------------------------------- */

/**
 * 把样式计划一次性落进文档：多个面板的 override、标注文字、页面尺寸
 * 合成**一条**历史记录（⌘Z 一次全部撤销），然后统一触发重渲染。
 */
export function applyStylePlan(plan: StylePlan, preset: StylePreset) {
  const touched = plan.panels.filter((p) => p.patches.length)
  if (!touched.length && !plan.annotationIds.length && !plan.subLabelIds.length && !plan.page) {
    status('该样式没有可应用的内容', 'error')
    return
  }
  commit(`应用样式「${preset.name}」`, (d) => {
    for (const { panel, patches } of touched) {
      const o = d.objects.find((x) => x.id === panel.id)
      if (o?.type !== 'panel') continue
      for (const p of patches) {
        o.overrides = o.overrides.filter((x) => !(x.gid === p.gid && x.prop === p.prop))
        o.overrides.push({ gid: p.gid, prop: p.prop, value: p.value })
      }
    }
    const applyText = (
      t: TextObject,
      s: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string },
    ) => {
      if (s.sizePt != null) t.sizePt = s.sizePt
      if (s.bold != null) t.bold = s.bold
      if (s.italic != null) t.italic = s.italic || undefined
      if (s.color != null) t.color = s.color
    }
    for (const id of plan.annotationIds) {
      const t = d.objects.find((x) => x.id === id)
      if (t?.type === 'text' && preset.annotation) applyText(t, preset.annotation)
    }
    for (const id of plan.subLabelIds) {
      const t = d.objects.find((x) => x.id === id)
      if (t?.type === 'text' && preset.subLabel) applyText(t, preset.subLabel)
    }
    if (plan.page) {
      d.page.w = clamp(plan.page.w, 10, 1000)
      d.page.h = clamp(plan.page.h, 10, 1000)
    }
  })
  for (const { panel } of touched) {
    const next = findObject(panel.id)
    if (next?.type === 'panel') requestRender(next.fileId, next.overrides, true)
  }
  const parts = [
    touched.length && `${touched.length} 个面板`,
    plan.annotationIds.length && `${plan.annotationIds.length} 条标注`,
    plan.subLabelIds.length && `${plan.subLabelIds.length} 个序号标签`,
    plan.page && '页面尺寸',
  ].filter(Boolean)
  status(`已应用样式「${preset.name}」到 ${parts.join('、')}（${modKey('Z')} 可整体撤销）`)
}

/* ------------------------------ 结构化布局组 -------------------------------- */

const LAYOUT_KIND_LABEL: Record<LayoutGroup['kind'], string> = {
  row: '行布局',
  col: '列布局',
  grid: '网格布局',
}

export function findLayoutGroup(id: string | undefined): LayoutGroup | undefined {
  if (!id) return undefined
  return doc().layoutGroups?.find((g) => g.id === id)
}

/** 选区所属的布局组（成员任选其一即可） */
export function selectionLayoutGroup(): LayoutGroup | undefined {
  const sel = selectedObjects()
  const gid = sel.find((o) => o.groupId && findLayoutGroup(o.groupId))?.groupId
  return findLayoutGroup(gid)
}

/**
 * 把当前选区变成布局组：复用轻量成组（拖动任一成员即整组移动），
 * 在其上登记排布约束，随后立即按阅读顺序排一次。
 */
export function createLayoutGroup(kind: LayoutGroup['kind']) {
  const ids = useSelectionStore.getState().ids
  const objs = selectedObjects().filter((o) => !o.hidden)
  if (objs.length < 2) {
    status('至少选中 2 个对象才能创建布局组')
    return
  }
  const gid = newId('g')
  const ordered = readingOrder(objs)
  const group: LayoutGroup = {
    id: gid,
    kind,
    order: ordered.map((o) => o.id),
    gap: 4,
    cols: kind === 'grid' ? Math.ceil(Math.sqrt(ordered.length)) : undefined,
    align: 'start',
    uniform: null,
  }
  commit(`创建${LAYOUT_KIND_LABEL[kind]}`, (d) => {
    for (const o of d.objects) if (ids.includes(o.id)) o.groupId = gid
    d.layoutGroups = [...(d.layoutGroups ?? []), group]
    applyReflowDraft(d, group)
  })
  status(`已创建${LAYOUT_KIND_LABEL[kind]}（${objs.length} 个成员）；改间距/列数会自动重排`)
}

/** 在 immer draft 里就地重排（创建 / 参数修改 / 自动触发共用） */
function applyReflowDraft(d: FigureDocument, group: LayoutGroup): number {
  const patches = reflowPatches(d, group)
  for (const p of patches) {
    const o = d.objects.find((x) => x.id === p.id)
    if (!o) continue
    o.x = p.x
    o.y = p.y
    if (p.w != null) o.w = p.w
    if (p.h != null) o.h = p.h
  }
  return patches.length
}

export function updateLayoutGroup(
  id: string,
  patch: Partial<Pick<LayoutGroup, 'kind' | 'gap' | 'cols' | 'align' | 'uniform'>>,
) {
  commit('调整布局组', (d) => {
    const g = d.layoutGroups?.find((x) => x.id === id)
    if (!g) return
    Object.assign(g, patch)
    applyReflowDraft(d, g)
  })
}

/** 手动「重新排列」：新成员、被挪开的成员都归位 */
export function reflowLayoutGroup(id: string) {
  const g = findLayoutGroup(id)
  if (!g) return
  commit('重新排列布局组', (d) => {
    const gg = d.layoutGroups?.find((x) => x.id === id)
    if (gg) applyReflowDraft(d, gg)
  })
}

/** 解散布局组：成员位置保持现状，只移除约束与成组 */
export function dissolveLayoutGroup(id: string) {
  commit('解散布局组', (d) => {
    d.layoutGroups = (d.layoutGroups ?? []).filter((g) => g.id !== id)
    for (const o of d.objects) if (o.groupId === id) o.groupId = undefined
  })
  status('已解散布局组，对象位置保持不变')
}

export function toggleLayoutPinned(ids: string[]) {
  if (!ids.length) return
  const anyUnpinned = selectedObjects().some((o) => ids.includes(o.id) && !o.layoutPinned)
  updateObjects(ids, anyUnpinned ? '固定位置（不随重排）' : '跟随布局约束', (o) => {
    o.layoutPinned = anyUnpinned ? true : undefined
  })
}

/**
 * 自动重排：订阅文档，成员**尺寸**变化（替换素材、改面板比例、等效缩放）后
 * 自动归位。位置变化不触发——手动拖动是用户显式意图；撤销/重做也不触发，
 * 否则一撤销就被排回去，undo 形同虚设。
 */
export function startLayoutAutoReflow(): () => void {
  let timer: number | undefined
  const store = useDocumentStore
  let prevDoc = store.getState().doc
  const signatures = new Map<string, string>()
  const snapshot = (d: FigureDocument) => {
    signatures.clear()
    for (const g of d.layoutGroups ?? []) signatures.set(g.id, sizeSignature(d, g))
  }
  snapshot(prevDoc)

  const unsub = store.subscribe((state, prev) => {
    if (state.doc === prevDoc) return
    const undoRedo =
      state.future.length > prev.future.length || // undo
      (state.past.length > prev.past.length && state.future.length < prev.future.length) // redo
    prevDoc = state.doc
    if (state.txn) return
    if (undoRedo) {
      snapshot(state.doc)
      return
    }
    const dirty = (state.doc.layoutGroups ?? []).filter(
      (g) => sizeSignature(state.doc, g) !== signatures.get(g.id),
    )
    snapshot(state.doc)
    if (!dirty.length) return
    window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      let moved = 0
      commit('自动重排布局组', (d) => {
        for (const g of dirty) {
          const gg = d.layoutGroups?.find((x) => x.id === g.id)
          if (gg) moved += applyReflowDraft(d, gg)
        }
      })
      snapshot(useDocumentStore.getState().doc)
      if (moved) status(`布局组已自动重排（${modKey('Z')} 可撤销）`)
    }, 120)
  })
  return () => {
    window.clearTimeout(timer)
    unsub()
  }
}

/* ========================================================================== */
/*  成组                                                                       */
/* ========================================================================== */

/** 该对象所在组的全部成员 id（无组则只有它自己），保持文档顺序 */
export function groupMates(id: string): string[] {
  const objs = doc().objects
  const self = objs.find((o) => o.id === id)
  if (!self?.groupId) return self ? [id] : []
  return objs.filter((o) => o.groupId === self.groupId).map((o) => o.id)
}

/**
 * 选区里这次真正能移动的对象。组的意义是「保持相对排布」，所以**组内任一成员
 * 锁定就整组不动**——只动没锁的那一半等于把组悄悄拆散（成员的锁定状态不限于
 * 选区内，按 groupId 全量扫文档）。不成组的锁定对象照旧逐个过滤。
 */
export function movableTargets(ids: string[]): {
  objects: CanvasObject[]
  /** 因含锁定成员被整组跳过的组数，调用方据此提示 */
  blockedGroups: number
} {
  const objs = doc().objects
  const expanded = expandGroups(ids)
  const lockedGids = new Set(
    objs.filter((o) => o.locked && o.groupId).map((o) => o.groupId as string),
  )
  const blocked = new Set<string>()
  const objects = objs.filter((o) => {
    if (!expanded.includes(o.id)) return false
    if (o.groupId && lockedGids.has(o.groupId)) {
      blocked.add(o.groupId)
      return false
    }
    return !o.locked
  })
  return { objects, blockedGroups: blocked.size }
}

/** 组因含锁定成员被跳过时的统一提示（移动 / 方向键微调共用） */
export function warnBlockedGroups(blockedGroups: number, movedAny: boolean) {
  if (!blockedGroups) return
  status(
    movedAny
      ? `组内有锁定对象，已跳过 ${blockedGroups} 个组`
      : '组内有锁定对象，先解锁才能移动整组',
  )
}

/** 选区补齐成整组：点中组里任意一个 = 选中整组 */
export function expandGroups(ids: string[]): string[] {
  const objs = doc().objects
  const gids = new Set(
    ids.map((id) => objs.find((o) => o.id === id)?.groupId).filter(Boolean) as string[],
  )
  if (!gids.size) return ids
  const out = [...ids]
  for (const o of objs) {
    if (o.groupId && gids.has(o.groupId) && !out.includes(o.id)) out.push(o.id)
  }
  return out
}

export function groupSelected() {
  const ids = useSelectionStore.getState().ids
  if (ids.length < 2) {
    status('至少选中 2 个对象才能成组')
    return
  }
  const gid = newId('g')
  updateObjects(ids, `成组 ${ids.length} 个对象`, (o) => {
    o.groupId = gid
  })
  status(`已成组 ${ids.length} 个对象，之后点其中之一会整组选中`)
}

export function ungroupSelected() {
  const ids = useSelectionStore.getState().ids
  const gids = new Set(
    selectedObjects().map((o) => o.groupId).filter(Boolean) as string[],
  )
  if (!gids.size) {
    status('选中的对象不在任何组里')
    return
  }
  commit('取消成组', (d) => {
    for (const o of d.objects) if (ids.includes(o.id)) o.groupId = undefined
    // 该组若带布局约束，成员散了约束也一并移除
    if (d.layoutGroups?.length) {
      d.layoutGroups = d.layoutGroups.filter(
        (g) => !gids.has(g.id) || d.objects.filter((o) => o.groupId === g.id).length >= 2,
      )
    }
  })
}

/** 选区里存在成组对象——决定「取消成组」是否可用 */
export const selectionHasGroup = () => selectedObjects().some((o) => o.groupId)

/* ========================================================================== */
/*  多选：参照目标对齐 / 分布 / 等宽等高 / 精确间距                              */
/* ========================================================================== */

/** 对齐的参照框：选区包围盒 / 整个画布 / 最后选中的那个对象 */
export type AlignRef = 'selection' | 'page' | 'primary'

export const ALIGN_REF_LABEL: Record<AlignRef, string> = {
  selection: '选区',
  page: '画布',
  primary: '最后选中',
}

const ALIGN_LABEL: Record<AlignMode, string> = {
  left: '左对齐',
  hcenter: '水平居中',
  right: '右对齐',
  top: '顶对齐',
  vcenter: '垂直居中',
  bottom: '底对齐',
  hdist: '水平等距',
  vdist: '垂直等距',
  samew: '等宽',
  sameh: '等高',
}

/** 在给定参照框里就地对齐；直接改传入对象（immer draft） */
function alignIn(objs: CanvasObject[], mode: AlignMode, box: Rect): void {
  if (mode === 'hdist' || mode === 'vdist') {
    const k = mode === 'hdist' ? 'x' : 'y'
    const s = mode === 'hdist' ? 'w' : 'h'
    const sorted = objs.slice().sort((a, b) => a[k] - b[k])
    const total = sorted.reduce((t, o) => t + o[s], 0)
    const gap = (box[s] - total) / (sorted.length - 1)
    let cur = box[k]
    for (const o of sorted) {
      o[k] = cur
      cur += o[s] + gap
    }
    return
  }
  if (mode === 'samew' || mode === 'sameh') {
    const target = mode === 'samew' ? box.w : box.h
    for (const o of objs) {
      if (mode === 'samew') {
        const k = target / o.w
        o.w = target
        if (o.type === 'panel' && panelAspectLocked(o)) o.h *= k
      } else {
        // 文字高度由内容决定，等高对它没有意义
        if (o.type === 'text') continue
        const k = target / o.h
        o.h = target
        if (o.type === 'panel' && panelAspectLocked(o)) o.w *= k
      }
    }
    return
  }
  for (const o of objs) {
    if (mode === 'left') o.x = box.x
    else if (mode === 'right') o.x = box.x + box.w - o.w
    else if (mode === 'hcenter') o.x = box.x + (box.w - o.w) / 2
    else if (mode === 'top') o.y = box.y
    else if (mode === 'bottom') o.y = box.y + box.h - o.h
    else if (mode === 'vcenter') o.y = box.y + (box.h - o.h) / 2
  }
}

/**
 * 带参照目标的对齐。单选时参照只能是画布（自己跟自己对齐没有意义），
 * 因此 alignSelectedTo(mode, 'page') 就是「对齐到画布」。
 */
export function alignSelectedTo(mode: AlignMode, ref: AlignRef) {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return
  if ((mode === 'hdist' || mode === 'vdist') && ids.length < 3) {
    status('等距分布需要至少选中 3 个对象')
    return
  }
  const primaryId = ids.at(-1)!
  commit(`${ALIGN_LABEL[mode]}（${ALIGN_REF_LABEL[ref]}）`, (d) => {
    const objs = d.objects.filter((o) => ids.includes(o.id))
    if (!objs.length) return
    const primary = objs.find((o) => o.id === primaryId) ?? objs[objs.length - 1]
    const box: Rect =
      ref === 'page'
        ? { x: 0, y: 0, w: d.page.w, h: d.page.h }
        : ref === 'primary'
          ? rectOf(primary)
          : (boundsOf(objs) ?? rectOf(primary))
    // 以某个对象为参照时它自己不动，否则等宽等高会把基准也改掉
    alignIn(ref === 'primary' ? objs.filter((o) => o !== primary) : objs, mode, box)
  })
}

/** 精确间距：按位置排序后依次贴齐，第一个对象保持不动 */
export function setSelectionSpacing(axis: 'x' | 'y', gap: number) {
  const ids = useSelectionStore.getState().ids
  if (ids.length < 2) return
  commit(axis === 'x' ? '设置水平间距' : '设置垂直间距', (d) => {
    const objs = d.objects.filter((o) => ids.includes(o.id))
    const s = axis === 'x' ? 'w' : 'h'
    const sorted = objs.slice().sort((a, b) => a[axis] - b[axis])
    let cur = sorted[0][axis]
    for (const o of sorted) {
      o[axis] = cur
      cur += o[s] + gap
    }
  })
}

/* ========================================================================== */
/*  复制 / 粘贴样式                                                            */
/* ========================================================================== */

type StyleClip =
  | { kind: 'panel'; crop?: CropRect; rotation?: PanelRotation; opacity?: number }
  | ({ kind: 'text' } & Partial<TextObject>)
  | ({ kind: 'arrow' } & Partial<ArrowObject>)
  | ({ kind: 'shape' } & Partial<ShapeObject>)

/** 每类对象参与样式复制的键（几何 x/y/w/h 与内容永不复制） */
const TEXT_STYLE_KEYS = ['sizePt', 'bold', 'italic', 'underline', 'color', 'align',
  'lineHeight', 'padding', 'bg', 'borderColor', 'borderPt', 'rotationDeg'] as const
const ARROW_STYLE_KEYS = ['strokePt', 'color', 'head', 'headStart', 'headEnd',
  'dash', 'rotationDeg'] as const
const SHAPE_STYLE_KEYS = ['strokePt', 'color', 'fill', 'cornerRadius',
  'fillOpacity', 'dash', 'rotationDeg'] as const

function pickKeys(src: object, keys: readonly string[]): Record<string, unknown> {
  const from = src as Record<string, unknown>
  return Object.fromEntries(keys.map((k) => [k, from[k]]))
}

function assignKeys(target: object, clip: object, keys: readonly string[]): void {
  const from = clip as Record<string, unknown>
  const to = target as Record<string, unknown>
  for (const k of keys) {
    if (from[k] === undefined) delete to[k]
    else to[k] = from[k]
  }
}

/** 样式剪贴板不属于文档，不进 undo；粘贴才是一条历史 */
let styleClip: StyleClip | null = null

export const styleClipKind = () => styleClip?.kind ?? null

export function copySelectionStyle() {
  const src = selectedObjects().at(-1)
  if (src?.type === 'panel') {
    styleClip = {
      kind: 'panel',
      crop: src.crop ? { ...src.crop } : undefined,
      rotation: src.rotation,
      opacity: src.opacity,
    }
    status('已复制面板样式（裁剪 / 旋转 / 不透明度）')
  } else if (src?.type === 'text') {
    styleClip = { kind: 'text', ...pickKeys(src, TEXT_STYLE_KEYS) }
    status('已复制文字样式（字号 / 粗斜下划线 / 颜色 / 行距 / 背景描边）')
  } else if (src?.type === 'arrow') {
    styleClip = { kind: 'arrow', ...pickKeys(src, ARROW_STYLE_KEYS) }
    status('已复制箭头样式（线宽 / 颜色 / 端型 / 线型）')
  } else if (src?.type === 'shape') {
    styleClip = { kind: 'shape', ...pickKeys(src, SHAPE_STYLE_KEYS) }
    status('已复制形状样式（描边 / 填充 / 圆角 / 线型）')
  } else {
    status('先选中要复制样式的对象', 'error')
  }
}

export function pasteSelectionStyle() {
  const clip = styleClip
  if (!clip) return
  const ids = selectedObjects()
    .filter((o) => o.type === clip.kind)
    .map((o) => o.id)
  if (!ids.length) {
    const kindLabel = { panel: '面板', text: '文字', arrow: '箭头', shape: '形状' }[clip.kind]
    status(`选区里没有可粘贴的${kindLabel}`, 'error')
    return
  }
  updateObjects(ids, '粘贴样式', (o) => {
    if (clip.kind === 'panel' && o.type === 'panel') {
      rotatePanelDraft(o, clip.rotation ?? 0)
      applyCropDraft(o, clip.crop)
      o.opacity = clip.opacity
    } else if (clip.kind === 'text' && o.type === 'text') {
      assignKeys(o, clip, TEXT_STYLE_KEYS)
    } else if (clip.kind === 'arrow' && o.type === 'arrow') {
      assignKeys(o, clip, ARROW_STYLE_KEYS)
    } else if (clip.kind === 'shape' && o.type === 'shape') {
      assignKeys(o, clip, SHAPE_STYLE_KEYS)
    }
  })
  status(`已粘贴样式到 ${ids.length} 个对象`)
}

/* ========================================================================== */
/*  面板几何：旋转 / 裁剪 / Fit / Fill                                          */
/* ========================================================================== */

const updatePanels = (ids: string[], label: string, patch: (o: PanelObject) => void) =>
  updateObjects(ids, label, (o) => {
    if (o.type === 'panel') patch(o)
  })

/** 写内容（未旋转）尺寸，映射回包围盒；锚定左上角，与拖手柄一致 */
function setContentSize(o: PanelObject, w: number, h: number): void {
  const swap = rotationSwaps(panelRotation(o))
  o.w = swap ? h : w
  o.h = swap ? w : h
}

/** 旋转到指定角度：包围盒绕自身中心交换宽高，内容与裁剪不变 */
export function rotatePanelDraft(o: PanelObject, next: PanelRotation): void {
  const cur = panelRotation(o)
  if (cur === next) return
  if (rotationSwaps(((next - cur + 360) % 360) as PanelRotation)) {
    const cx = o.x + o.w / 2
    const cy = o.y + o.h / 2
    const w = o.h
    const h = o.w
    o.x = cx - w / 2
    o.y = cy - h / 2
    o.w = w
    o.h = h
  }
  o.rotation = next === 0 ? undefined : next
}

/**
 * 换裁剪框：**完整图在页面上的落位保持不动**，只有露出的部分变。
 * 与画布上拖裁剪框、以及原来的「重置裁剪」是同一套语义。
 */
export function applyCropDraft(o: PanelObject, next?: CropRect): void {
  const rot = panelRotation(o)
  const cur = o.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const to = next ?? { x: 0, y: 0, w: 1, h: 1 }
  const content = panelContentSize(o)
  const fullW = content.w / cur.w
  const fullH = content.h / cur.h
  // 可见区中心在完整图里挪了多少（内容空间 → 页面空间）
  const [pdx, pdy] = rotateVec(
    (to.x + to.w / 2 - (cur.x + cur.w / 2)) * fullW,
    (to.y + to.h / 2 - (cur.y + cur.h / 2)) * fullH,
    rot,
  )
  const cx = o.x + o.w / 2 + pdx
  const cy = o.y + o.h / 2 + pdy
  setContentSize(o, fullW * to.w, fullH * to.h)
  o.x = cx - o.w / 2
  o.y = cy - o.h / 2
  o.crop = to.w >= 1 && to.h >= 1 ? undefined : { ...to }
}

export function rotatePanels(ids: string[], next: PanelRotation) {
  updatePanels(ids, next === 0 ? '取消旋转' : `旋转 ${next}°`, (o) => rotatePanelDraft(o, next))
}

export function setPanelOpacity(ids: string[], v: number) {
  const opacity = clamp(v, 0, 1)
  updatePanels(ids, '修改不透明度', (o) => {
    o.opacity = opacity >= 1 ? undefined : opacity
  })
}

export function setPanelAspectLocked(ids: string[], locked: boolean) {
  updatePanels(ids, locked ? '锁定宽高比' : '解锁宽高比', (o) => {
    o.aspectLocked = locked ? undefined : false
  })
}

export function resetPanelCrop(ids: string[]) {
  updatePanels(ids, '重置裁剪', (o) => {
    if (o.crop) applyCropDraft(o, undefined)
  })
}

/** Fit：清掉裁剪，整图等比缩到当前框内，框跟着收成图的比例（居中） */
export function fitPanels(ids: string[]) {
  updatePanels(ids, '完整放入框内', (o) => {
    const box = panelContentSize(o)
    const ar = o.nativeW / o.nativeH
    let w = box.w
    let h = box.w / ar
    if (h > box.h) {
      h = box.h
      w = box.h * ar
    }
    const cx = o.x + o.w / 2
    const cy = o.y + o.h / 2
    applyCropDraft(o, undefined)
    setContentSize(o, w, h)
    o.x = cx - o.w / 2
    o.y = cy - o.h / 2
  })
}

/** Fill：框一点不动，用居中裁剪把溢出的部分切掉 */
export function fillPanels(ids: string[]) {
  updatePanels(ids, '填满当前框', (o) => {
    const box = panelContentSize(o)
    const r = box.w / box.h
    const a = o.nativeW / o.nativeH
    const k = r >= a ? a / r : r / a
    o.crop = r >= a ? { x: 0, y: (1 - k) / 2, w: 1, h: k } : { x: (1 - k) / 2, y: 0, w: k, h: 1 }
  })
}

/** 恢复原始比例：保持内容宽度，按（裁剪后的）原始长宽比修高度 */
export function restorePanelAspect(ids: string[]) {
  updatePanels(ids, '恢复原始比例', (o) => {
    const w = panelContentSize(o).w
    const ar = (o.nativeW * (o.crop?.w ?? 1)) / (o.nativeH * (o.crop?.h ?? 1))
    setContentSize(o, w, w / ar)
  })
}

/** 恢复原始尺寸：回到素材自身的 mm 尺寸（裁剪比例仍生效） */
export function restorePanelNativeSize(ids: string[]) {
  updatePanels(ids, '恢复原始尺寸', (o) => {
    setContentSize(o, o.nativeW * (o.crop?.w ?? 1), o.nativeH * (o.crop?.h ?? 1))
  })
}

/**
 * 替换素材：保留 X/Y/W/H、裁剪、旋转、不透明度与层级，只换图源。
 * 图内修改（override）无法跨脚本搬运，有的话先征求同意再清空。
 */
export async function replacePanelAsset(panelId: string, info: PanelInfo): Promise<boolean> {
  const panel = findObject(panelId)
  if (panel?.type !== 'panel') return false
  if (panel.fileId === info.id) return false
  if (
    panel.overrides.length &&
    !(await askConfirm({
      title: `替换为「${info.name}」？`,
      body: `当前面板有 ${panel.overrides.length} 项图内修改。这些修改绑定在原脚本的元素上，换素材后无法保留，将被清空（可撤销）。位置、尺寸、裁剪与层级都会保留。`,
      confirmLabel: '替换并清空修改',
      danger: true,
    }))
  ) {
    return false
  }
  if (useUiStore.getState().elementPanelId === panelId) {
    useUiStore.getState().setElementPanel(null)
  }
  updateObject<PanelObject>(panelId, `替换素材为 ${info.name}`, (o) => {
    o.fileId = info.id
    o.fileKind = info.kind
    o.nativeW = info.native_w_mm
    o.nativeH = info.native_h_mm
    o.pxW = info.px_w
    o.script = info.script ?? null
    o.cost = info.cost
    o.name = info.name
    o.overrides = info.baked_overrides ? structuredClone(info.baked_overrides) : []
  })
  useAssetStore.getState().markUsed(info.id)
  status(`已替换为「${info.name}」，位置与尺寸保持不变`)
  return true
}
