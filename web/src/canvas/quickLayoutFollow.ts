import { isSubLabel } from '@/lib/stylePresets'
import { useDocumentStore } from '@/store/documentStore'
import { displayedExactManifest, useMountedSvgStore } from '@/store/mountedSvgStore'
import { useRenderStore } from '@/store/renderStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { PanelObject, QuickLayoutMemo } from '@/types/document'

/**
 * 拖行 / 列分隔线时图本身跟手（ADR 0199 修订，假实时预览规则）。
 *
 * **只写 CSS 的 `translate` / `scale` 两个独立属性**（外加 panel 的 `transform-origin`）：React 的 `ObjectView`
 * 只管 `left / top / width / height / transform`，不碰这几个，所以 commit 之后 React 把文档里的新框写上来时
 * 不会冲掉预览，收工时也只要把这几个属性放回出发时的值，不用猜 React 该写什么。
 *
 * 位移与缩放永远从**文档里此刻的框**现算（`base → 预览框`），不累加：commit 之后文档里的框先变（位置先到、
 * 尺寸等新图渲染回来才跟上），`MutationObserver` 在 React 每次改了 `style` 的同一个微任务里重算——浏览器
 * 画的每一帧，图的外框都在预览框上，不会出现「位置对了、尺寸还是旧的」那一帧。
 *
 * 生命周期：`update`（拖动中，只改 DOM）→ `restore`（取消 / 失败 / 作废，原样放回）或
 * `onCommitted` → 保持到「每张图的文档框与 DOM 框都落在最终框的 1 px 内、且新图已挂上」才同一帧放手；
 * 兜底 `HOLD_CAP_MS` 后无论如何放手。子图标签（文档里挂在这些图旁边的 `(a)` 之类）跟着它那张图的左上角走。
 */
export const HOLD_CAP_MS = 8000
/** 文档框 / DOM 框与最终框差多少屏幕 px 以内算落位 */
const SETTLE_PX = 1

type Box = [number, number, number, number]

interface Entry {
  id: string
  kind: 'panel' | 'label'
  el: HTMLElement
  /** 标签：它跟的那张图 */
  owner?: string
  /** 出发时 style 上的值（原样放回） */
  orig: { translate: string; scale: string; origin: string }
}

const px = (n: number) => `${Math.round(n * 1000) / 1000}px`

export class QuickLayoutFollow {
  private entries: Entry[] = []
  private base: Record<string, Box> = {}
  private target: Record<string, Box> = {}
  private observer: MutationObserver | null = null
  private unsubs: (() => void)[] = []
  private timer: ReturnType<typeof setTimeout> | null = null
  private holding = false
  private ended = false

  /** 收集这份排版记忆里的图与它们旁边的子图标签的 DOM 节点（没挂上的图就没有跟手，退回淡色轮廓） */
  constructor(memo: QuickLayoutMemo) {
    this.base = Object.fromEntries(Object.entries(memo.boxes).map(([id, b]) => [id, [...b] as Box]))
    const doc = useDocumentStore.getState().doc
    const near = (x: number, y: number, [bx, by, bw, bh]: Box) =>
      x >= bx - 5 && x <= bx + bw + 5 && y >= by - 5 && y <= by + bh + 5
    for (const id of Object.keys(memo.cells)) {
      const el = this.query(id)
      if (el) this.entries.push(this.entryOf(id, 'panel', el))
    }
    const followed = new Set(this.entries.map((e) => e.id))
    for (const o of doc.objects) {
      if (o.type !== 'text' || !isSubLabel(o)) continue
      let owner: string | undefined
      let best = Infinity
      for (const [id, b] of Object.entries(this.base)) {
        if (!followed.has(id) || !near(o.x, o.y, b)) continue
        const d = Math.hypot(o.x - b[0], o.y - b[1])
        if (d < best) {
          best = d
          owner = id
        }
      }
      const el = owner ? this.query(o.id) : null
      if (el && owner) this.entries.push({ ...this.entryOf(o.id, 'label', el), owner })
    }
  }

  /** 真正在跟手的图 id（不画淡色轮廓的那几张） */
  get followedIds(): string[] {
    return this.entries.filter((e) => e.kind === 'panel').map((e) => e.id)
  }

  private query(id: string): HTMLElement | null {
    return document.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(id)}"]`)
  }

  private entryOf(id: string, kind: Entry['kind'], el: HTMLElement): Entry {
    return { id, kind, el, orig: { translate: el.style.translate, scale: el.style.scale, origin: el.style.transformOrigin } }
  }

  /** 拖动中：新的预览框（mm）。只改 DOM；用 base（文档里此刻的框）现算 */
  update(boxes: Record<string, Box>): void {
    if (this.ended) return
    this.target = boxes
    this.ensureObserver()
    this.apply()
  }

  /** 取消 / 失败 / 作废：每个节点的 `translate` / `scale` / `transform-origin` 原样放回 */
  restore(): void {
    if (this.ended) return
    this.ended = true
    this.teardown()
    for (const e of this.entries) {
      e.el.style.translate = e.orig.translate
      e.el.style.scale = e.orig.scale
      e.el.style.transformOrigin = e.orig.origin
    }
  }

  /** 方案已 commit：交最终的框；保持到图真正落位（或兜底超时）再放手 */
  onCommitted(boxes: Record<string, Box>): void {
    if (this.ended) return
    this.target = { ...this.target, ...boxes }
    this.holding = true
    this.ensureObserver()
    const check = () => this.settle()
    this.unsubs.push(
      useDocumentStore.subscribe(check),
      useRenderStore.subscribe(check),
      useMountedSvgStore.subscribe(check),
    )
    this.timer = setTimeout(() => this.restore(), HOLD_CAP_MS)
    this.apply()
    this.settle()
  }

  private ensureObserver() {
    if (this.observer || typeof MutationObserver === 'undefined') return
    this.observer = new MutationObserver(() => {
      this.apply()
      this.settle()
    })
    for (const e of this.entries) this.observer.observe(e.el, { attributes: true, attributeFilter: ['style'] })
  }

  private teardown() {
    this.observer?.disconnect()
    this.observer = null
    for (const u of this.unsubs.splice(0)) u()
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }

  /** 节点被换掉（重新挂载）就重新找一个；找不到的丢掉 */
  private refresh() {
    for (const e of this.entries) {
      if (e.el.isConnected) continue
      const el = this.query(e.id)
      if (el) {
        e.el = el
        this.observer?.observe(el, { attributes: true, attributeFilter: ['style'] })
      }
    }
    this.entries = this.entries.filter((e) => e.el.isConnected)
  }

  private apply() {
    this.refresh()
    const doc = useDocumentStore.getState().doc
    for (const e of this.entries) {
      if (e.kind === 'panel') {
        const o = doc.objects.find((x) => x.id === e.id)
        const b = this.target[e.id]
        if (!b || o?.type !== 'panel') continue
        const ow = mmToWorld(o.w)
        const oh = mmToWorld(o.h)
        if (!(ow > 0) || !(oh > 0)) continue
        const sx = mmToWorld(b[2]) / ow
        const sy = mmToWorld(b[3]) / oh
        // 元素自己的 transform 是 translate(X, Y)（原点 0 0）：终点 = T + S·(X, Y)，要等于预览框左上角
        const tx = px(mmToWorld(b[0]) - sx * mmToWorld(o.x))
        const ty = px(mmToWorld(b[1]) - sy * mmToWorld(o.y))
        this.set(e.el, 'translate', `${tx} ${ty}`)
        this.set(e.el, 'scale', `${Math.round(sx * 1e6) / 1e6} ${Math.round(sy * 1e6) / 1e6}`)
        this.set(e.el, 'transformOrigin', '0 0')
      } else if (e.owner) {
        const from = this.base[e.owner]
        const to = this.target[e.owner]
        if (!from || !to) continue
        this.set(e.el, 'translate', `${px(mmToWorld(to[0] - from[0]))} ${px(mmToWorld(to[1] - from[1]))}`)
      }
    }
  }

  private set(el: HTMLElement, prop: 'translate' | 'scale' | 'transformOrigin', value: string) {
    if (el.style[prop] !== value) el.style[prop] = value
  }

  /** 每张图的文档框与 DOM 框都落在最终框 1 px 内、新图已挂上 = 放手（与 DOM 更新同一帧） */
  private settle() {
    if (!this.holding || this.ended) return
    const zoom = useViewportStore.getState().zoom
    const within = (a: number, b: number) => Math.abs(a - b) * zoom <= SETTLE_PX
    const doc = useDocumentStore.getState().doc
    for (const e of this.entries) {
      if (e.kind !== 'panel') continue
      const b = this.target[e.id]
      const o = doc.objects.find((x) => x.id === e.id)
      if (!b || !o || o.type !== 'panel') continue
      if (![o.x - b[0], o.y - b[1], o.w - b[2], o.h - b[3]].every((d) => within(mmToWorld(d), 0))) return
      // DOM 里 React 写的宽高也到了（位置与宽高在同一次提交里写）
      if (!within(parseFloat(e.el.style.width), mmToWorld(b[2])) || !within(parseFloat(e.el.style.height), mmToWorld(b[3]))) return
      if (!displayedExactManifest(o as PanelObject)) return
    }
    this.restore()
  }
}
