/**
 * 引擎渲染的**调度器**：把「什么时候麻烦 matplotlib」这件事从 React 里拿出来。
 *
 * 只做四件事：合并同一面板的连续请求（防抖）、立即发、本轮不发只占位、手势结束时
 * 定稿（flush）。状态只有一张按面板索引的计时器表；渲染态本身仍只在 `renderStore`
 * 一份，文档改动仍只经 `documentStore.commit`——这里不建第二套渲染状态。
 *
 * 依赖方向是单向的：`store/actions` 与检查器调这里，这里只调 `renderStore` /
 * `documentStore`，**绝不 import `actions`**（否则只是把 actions ↔ useEngineSync 那个
 * 两节点环换成三节点环）。`hooks/useEngineSync` 只剩订阅与生命周期装配。
 */
import { useDocumentStore } from '@/store/documentStore'
import { panelRender, renderEpoch, renderKeyOf, useRenderStore } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'

/** 文字/数值输入合并成一次渲染的窗口；颜色、开关、拖动结束走 immediate */
export const DEBOUNCE_MS = 300

/**
 * 连续调整期间的预览 dpi。**只给含图像（imshow 等）的面板**：实测那里
 * 200→100 让一次渲染的往返降 16%、SVG 体积降 75%；纯矢量面板上耗时与字节数
 * 完全相同（docs/perf-baseline.md 补测两张表），给了只会白白让图变糊。
 * 定稿（immediate / flushRender）永远用默认 dpi。
 */
export const INTERACTIVE_PREVIEW_DPI = 100

/**
 * 防抖计时器按**面板**索引，不按变体：连着改同一个值会走出一串变体键，
 * 按变体存的话每个中间值都会在 300ms 后各渲染一次（打十个字 = 十次渲染）。
 * 也不能按文件——同文件的两个副本各调各的，互相取消就会有一个永远渲染不出来。
 */
const timers = new Map<string, number>()

/** 该面板的图里有没有位图元素（imshow / 图片）——降质预览只对它们有收益 */
function hasImageElement(panel: PanelObject): boolean {
  // 走 panelRender：变体刚换、自己那份还没画出来时退回文件最近那份。
  // 元素构成不随 override 的取值变化，用哪个变体的 manifest 判断都一样
  const manifest = panelRender(useRenderStore.getState(), panel)?.manifest
  return !!manifest?.elements.some((el) => el.role === 'image')
}

/**
 * 渲染策略。**与历史无关**——无论选哪个，文档改动都已经经过
 * documentStore.commit 进了历史；这里决定的只是「什么时候麻烦 matplotlib」。
 *
 *   immediate  立刻发（定稿：松手、颜色定稿、枚举、撤销/重做）
 *   defer      防抖 300ms 后发（打字、连续数值输入）
 *   none       **本轮完全不发**，只登记 wantPatches 占位；由 flushRender
 *              在手势结束时定稿。假实时的 scrub / 取色走这条：拖动期间
 *              画面由 SVG 局部预览负责，matplotlib 一次都不用跑。
 *
 * `none` 必须照样写 wantPatches：syncEngine 的跳过判据就是它，不占位的话
 * 同步 effect 会立刻替这次改动发一次 immediate 渲染——比不加策略还糟。
 */
export type RenderPolicy = 'immediate' | 'defer' | 'none'

const policyOf = (p: boolean | RenderPolicy | undefined): RenderPolicy =>
  p === true ? 'immediate' : p === false || p == null ? 'defer' : p

/** 取消该面板挂起的防抖渲染（占位的 wantPatches 不动——那是同步器的跳过判据）。 */
export function cancelScheduledRender(panelId: string): void {
  window.clearTimeout(timers.get(panelId))
  timers.delete(panelId)
}

/** 有没有面板还挂着防抖计时器（测试与诊断用；产品路径不读它）。 */
export function hasScheduledRender(panelId: string): boolean {
  return timers.has(panelId)
}

/**
 * 请求渲染。同一面板的连续请求会被合并：debounce 期内只保留最后一次，
 * 真正发出后由 renderStore 的 busy/queued 再兜一层。
 */
export function requestRender(panel: PanelObject, immediate: boolean | RenderPolicy = false) {
  const policy = policyOf(immediate)
  const store = useRenderStore.getState()
  const key = renderKeyOf(panel)
  const want = JSON.stringify(panel.overrides)
  // 值没变就别写 store：patch() 会换掉 byKey 的引用，把依赖它的 effect
  // 全部重跑一遍——白白多一轮渲染，也是同步循环的燃料
  if (store.get(key).wantPatches !== want) {
    store.patch(key, { fileId: panel.fileId, wantPatches: want })
  }

  // 防抖那一路是「还在调」，可以先给一张低清；immediate 是定稿，永远默认 dpi
  const dpi = policy !== 'defer' || !hasImageElement(panel) ? undefined : INTERACTIVE_PREVIEW_DPI
  const patches = panel.overrides
  const fileId = panel.fileId
  // 项目代际在**排渲染这一刻**取：防抖窗口里切了项目的话，到点那一次整个作废
  const epoch = renderEpoch()
  const fire = () => {
    timers.delete(panel.id)
    void store.render(fileId, patches, dpi, policy, epoch)
  }
  cancelScheduledRender(panel.id)
  if (policy === 'none') return
  if (policy === 'immediate') fire()
  else timers.set(panel.id, window.setTimeout(fire, DEBOUNCE_MS))
}

/**
 * 立刻冲刷该面板挂起的渲染，并保证最终那张是定稿质量（松开滑块、退出输入框）。
 *
 * 两件事都必须做：挂起的那次直接发出去；已经画完但用的是降质 dpi 的，
 * 补一张默认 dpi 的——否则用户手一松，图就永远停在临时低清上。
 */
export function flushRender(panelId: string) {
  // 按 id 从文档里现取，不信调用方手里那份：事件处理器闭包里的 panel 可能是
  // 上一帧的，拿它的 overrides 去渲染就等于把刚改的那一版丢了（而挂起的
  // 计时器已经被清掉，同步器又因为 wantPatches 相等而跳过 → 永远画不出来）
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') return
  const store = useRenderStore.getState()
  cancelScheduledRender(panelId)
  const want = JSON.stringify(panel.overrides)
  const state = store.get(renderKeyOf(panel))
  // 判据是「这一版还没画出来」，不是「有没有挂起的计时器」。
  // 旧实现只看计时器：render:'none' 的手势（scrub / 取色）压根不设计时器，
  // 松手时就会一声不响地什么都不做——占位的 wantPatches 还挡着同步器，
  // 结果是用户改完之后**永远等不到那张定稿图**。
  if (state.lastPatches !== want) {
    void store.render(panel.fileId, panel.overrides, undefined, 'sync')
    return
  }
  // 已经是这一版了：只有「现在这张是拖动期的低清」才需要补一张定稿
  if (state.previewDpi != null) {
    void store.render(panel.fileId, panel.overrides, undefined, 'sync')
  }
}
