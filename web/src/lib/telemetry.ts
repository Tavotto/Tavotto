/**
 * 前端这一侧的匿名用量统计：**只是一层薄薄的转发**。
 *
 * 同意态、install_id、白名单、投递、队列全在后端
 * （`src/tavotto/engine/telemetry.py`）。这里只做三件事：
 *   ① 缓存「现在发不发」，免得每次编辑都白跑一次 HTTP；
 *   ② 把语义事件转给 `/api/telemetry/event`；
 *   ③ 把历史标签 key 映射成一个**闭集**里的 edit_kind。
 *
 * 为什么服务端能推断的事件不放这里：前端记的是「用户点了导出」，点了之后还
 * 可能失败。导出 / AI / 升级这三件的成功边界都在后端，就在后端记
 * （见 docs/analytics/telemetry-events.md）。
 *
 * **失败一律安静**：埋点是可选的、失败无所谓的功能，绝不能让一次
 * fetch 异常冒进拖动、撤销或导出的调用栈里。
 */
import { postTelemetryEvent } from '@/lib/api'

/** 后端说了算；这里只是本地缓存，默认 false（没问过 = 不发） */
let shareUsage = false

export function setTelemetryEnabled(value: boolean): void {
  shareUsage = value
}

export function telemetryEnabled(): boolean {
  return shareUsage
}

/**
 * 记一条语义事件。**永不抛异常、永不 await**——调用点全在交互路径上。
 */
export function captureTelemetry(
  event: string,
  properties: Record<string, unknown> = {},
): void {
  if (!shareUsage) return
  try {
    void postTelemetryEvent(event, properties).catch(() => {})
  } catch {
    /* 埋点失败绝不影响任何一次编辑 */
  }
}

/* -------------------------------------------------------------------------- */
/*  一次编辑属于哪一类                                                          */
/* -------------------------------------------------------------------------- */

export type EditKind =
  | 'text'
  | 'series'
  | 'axes'
  | 'annotation'
  | 'layout'
  | 'style'
  | 'other'

/**
 * 历史标签的 key → 粗分类。
 *
 * **判据是开发者自己写死的那个 key，不是标签文案，更不是补丁内容**：
 * 文案会被翻译、会带上用户的文件名与属性名；补丁里装的是用户的数据。
 * key 是我们自己控制的稳定标识，映射不到就落 `other`——发出去的值永远来自
 * 下面这张闭表，新增一个历史标签最坏的后果是它被记成 `other`，绝不会变成
 * 一段自由文本。
 *
 * 已知的粗糙之处，如实记在 docs/analytics/telemetry-events.md 里：
 * `setProp` / `clearProp` 是图内元素属性编辑的通用入口，一条 key 同时覆盖
 * 字号、颜色、刻度、可见性…… 想再细分就得把 matplotlib 的属性名带进埋点，
 * 那正是白名单要挡住的东西，所以它们统一落 `style`。
 */
const EDIT_KIND_BY_LABEL: Record<string, EditKind> = {
  // 文字内容
  editText: 'text',
  insertSymbol: 'text',
  addText: 'text',
  addSubLabels: 'text',
  // 画布标注（箭头 / 形状 / 参考线）
  addArrow: 'annotation',
  addShape: 'annotation',
  insertShape: 'annotation',
  insertPreset: 'annotation',
  arrowEndpoint: 'annotation',
  lineEndpoint: 'annotation',
  addGuide: 'annotation',
  moveGuide: 'annotation',
  deleteGuide: 'annotation',
  clearGuides: 'annotation',
  // 版面
  addPanel: 'layout',
  moveObject: 'layout',
  moveObjects: 'layout',
  moveElement: 'layout',
  moveElements: 'layout',
  resizeObjects: 'layout',
  rotate: 'layout',
  rotateReset: 'layout',
  spacingX: 'layout',
  spacingY: 'layout',
  alignWithRef: 'layout',
  zTop: 'layout',
  zUp: 'layout',
  zDown: 'layout',
  zBottom: 'layout',
  reorderLayers: 'layout',
  group: 'layout',
  ungroup: 'layout',
  pinLayout: 'layout',
  unpinLayout: 'layout',
  createLayoutGroup: 'layout',
  dissolveLayoutGroup: 'layout',
  updateLayoutGroup: 'layout',
  reflowLayoutGroup: 'layout',
  autoReflow: 'layout',
  setPageSize: 'layout',
  fitPanel: 'layout',
  fillPanel: 'layout',
  restoreNativeSize: 'layout',
  restoreAspect: 'layout',
  lockAspect: 'layout',
  unlockAspect: 'layout',
  adjustCrop: 'layout',
  resetCrop: 'layout',
  duplicateObjects: 'layout',
  pasteObjects: 'layout',
  deleteObject: 'layout',
  deleteObjects: 'layout',
  hideObject: 'layout',
  showObject: 'layout',
  lockObject: 'layout',
  unlockObject: 'layout',
  renameObject: 'layout',
  replaceAsset: 'layout',
  relinkAssets: 'layout',
  // 坐标轴几何
  resizeAxes: 'axes',
  // 样式（含图内元素属性的通用入口，见上面的说明）
  applyStyle: 'style',
  pasteStyle: 'style',
  setOpacity: 'style',
  setProp: 'style',
  clearProp: 'style',
  resetOverrides: 'style',
  hideElements: 'style',
  unhideElement: 'style',
  seedBaked: 'style',
}

export function classifyEditKind(labelKey: string | undefined): EditKind {
  if (!labelKey) return 'other'
  // 标签形如 `history.setProp`；复数形态的 key 带 `_other` 后缀
  const tail = labelKey.split('.').pop() ?? ''
  const base = tail.replace(/_(zero|one|two|few|many|other)$/, '')
  return EDIT_KIND_BY_LABEL[base] ?? 'other'
}

/** patch_count 的上限与后端白名单同源（超出的截断，绝不发一个被拒的值） */
export const MAX_PATCH_COUNT = 1000

export function boundedCount(n: number): number {
  if (!Number.isFinite(n) || n < 0) return 0
  return Math.min(Math.floor(n), MAX_PATCH_COUNT)
}
