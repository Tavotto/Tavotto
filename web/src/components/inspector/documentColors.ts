import { useMemo } from 'react'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import type { CanvasObject, PageSetup, PanelObject } from '@/types/document'
import { normalizeHex } from '../ui/colorPalette'

/** 一张画布里取色要看的两样：对象与页面 */
type CanvasColors = { objects: readonly CanvasObject[]; page: PageSetup }

/** 这条图内 override 是不是一个颜色属性（判据见 `colorOverrideKeysOf`） */
export type ColorOverridePredicate = (panel: PanelObject, gid: string, prop: string) => boolean

const NO_OVERRIDE_COLORS: ColorOverridePredicate = () => false

/**
 * 「排版里的颜色」（界面名词，ADR 0001）：这份排版里已经在用的颜色，按出现次数从多到少
 * （`ColorField` 取色面板的第一组）。一份排版可以有多张画布：**全部画布**都算（Codex #829 P2）。
 *
 * 只读排版里**写下来的**颜色：画布对象（文字 / 箭头 / 形状的边与填充、文字底色与描边）、页面背景、
 * 各张图里用户改过的**颜色属性**的 override。图内 override 只收 `isColorOverride` 认定是颜色的那几条——
 * 标题文字改成 "abc" 也长得像色号，不能被当成 #aabbcc（Codex #829 P2）。脚本自己画的颜色不在排版里，这里不猜。
 */
export function documentColorsOf(
  canvases: CanvasColors | readonly CanvasColors[],
  isColorOverride: ColorOverridePredicate = NO_OVERRIDE_COLORS,
): string[] {
  const counts = new Map<string, number>()
  const add = (v: unknown) => {
    if (typeof v !== 'string') return
    const c = normalizeHex(v)
    if (c) counts.set(c, (counts.get(c) ?? 0) + 1)
  }
  const list: readonly CanvasColors[] = Array.isArray(canvases) ? canvases : [canvases as CanvasColors]
  for (const canvas of list) {
    for (const o of canvas.objects) {
      if (o.type === 'text') {
        add(o.color)
        add(o.bg)
        add(o.borderColor)
      } else if (o.type === 'arrow') add(o.color)
      else if (o.type === 'shape') {
        add(o.color)
        add(o.fill)
      } else if (o.type === 'panel') {
        for (const ov of o.overrides) if (isColorOverride(o, ov.gid, ov.prop)) add(ov.value)
      }
    }
    if (!canvas.page.transparent) add(canvas.page.bg)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c)
}

const overrideKey = (panelId: string, gid: string, prop: string) => `${panelId}\u0000${gid}\u0000${prop}`

/**
 * 哪些图内 override 是颜色属性：**判据只认 manifest**——那个元素的可编辑字段里，这个 prop 的
 * `type === 'color'`（引擎发的字段类型，与属性栏摆 `ColorField` 同一份事实）。前端不手抄一张
 * 「哪些名字是颜色」的表。manifest 取这一版、没有就取同文件上一版（只读显示用，不写几何）；
 * 都没有就不收（不猜）。返回一串排好序的键，选择器拿它当原始值比较，渲染态别的变化不触发重算。
 */
export function colorOverrideKeysOf(
  panels: readonly PanelObject[],
  state: { byKey: Record<string, { manifest?: Manifest | null } | undefined>; latest: Record<string, string> },
): string {
  const keys: string[] = []
  for (const p of panels) {
    if (p.overrides.length === 0) continue
    const manifest = state.byKey[renderKeyOf(p)]?.manifest ?? state.byKey[state.latest[p.fileId] ?? '']?.manifest
    if (!manifest) continue
    for (const ov of p.overrides) {
      const el = manifest.elements.find((e) => e.gid === ov.gid)
      if (el?.editable.some((f) => f.prop === ov.prop && f.type === 'color')) keys.push(overrideKey(p.id, ov.gid, ov.prop))
    }
  }
  return keys.sort().join('\n')
}

export function useDocumentColors(): string[] {
  const objects = useDocumentStore((s) => s.doc.objects)
  const page = useDocumentStore((s) => s.doc.page)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  // 激活画布以活跃的 doc 为准，其余画布用最后同步的快照（与 buildProject 同一条规则）
  const all = useMemo<CanvasColors[]>(
    () => [
      { objects, page },
      ...canvases.filter((c) => c.id !== activeId).map((c) => ({ objects: c.objects, page: c.page })),
    ],
    [objects, page, canvases, activeId],
  )
  const panels = useMemo(
    () => all.flatMap((c) => c.objects.filter((o): o is PanelObject => o.type === 'panel')),
    [all],
  )
  const colorKeys = useRenderStore((s) => colorOverrideKeysOf(panels, s))
  return useMemo(() => {
    const set = new Set(colorKeys ? colorKeys.split('\n') : [])
    return documentColorsOf(all, (p, gid, prop) => set.has(overrideKey(p.id, gid, prop)))
  }, [all, colorKeys])
}
