import type { Manifest } from './api'
import { t } from '@/i18n'
import type { FigureDocument, PanelObject, PanelOverride, TextObject } from '@/types/document'

/**
 * 论文样式预设：一组可复用的排版规格（字号/线宽/刻度/图例/配色/页面）。
 *
 * 应用是纯前端映射：按角色把预设值翻译成图内元素 override 与画布标注属性，
 * 走与手动编辑完全相同的通路（PanelObject.overrides + TextObject 字段），
 * 因此天然进撤销、天然不写回源文件。
 */

/**
 * 一份样式的**内容**（信封里的 `data`）。名字、id、revision 在信封上
 * （`ProfileRecord`），不在这里——内容与身份混在一起时，「改个名字」会变成
 * 一次内容修改，乐观并发就再也分不清两个窗口在争什么。
 */
export interface StyleProfileData {
  /** role → prop → value。只登记用户明确要统一的项。 */
  element: Record<string, Record<string, unknown>>
  /** 系列配色（按曲线/散点/柱形在图内的出现顺序循环取色）；空 = 不动配色 */
  palette?: string[]
  /** 画布标注文字样式 */
  annotation?: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string }
  /** 子图序号标签 (a)(b)(c) 样式（按内容 ^(x)$ 识别） */
  subLabel?: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string }
  /** 页面预设 */
  page?: { w: number; h: number }
  /** 默认画布背景色；不设 = 不动背景 */
  background?: string
  /** 这份样式是从哪套规范派生的（内置样式用；只作说明） */
  derived_from_spec?: string
  /** 导入 / 迁移时没能映射的字段：**留着，不丢**（界面把它记成一条 warning） */
  extra?: Record<string, unknown>
}

/**
 * 编辑中的一份样式（信封 + 内容摊平）。**只活在界面里**：存盘时拆回
 * `{display_name, data}`，见 `profileToDraft` / `draftToData`。
 */
export interface StylePreset extends StyleProfileData {
  id?: string
  name: string
}

/**
 * 预设里允许出现的 role → props 白名单（与 manifest 的 editable 字段一一对应）。
 * **加一项之前先确认 manifest 真的暴露了它**——白名单里多一个渲染层没有的
 * prop，应用时只会安静地进 `unmappable`，用户以为设了、其实什么都没发生。
 */
export const STYLE_ROLE_PROPS: Record<string, string[]> = {
  text: ['fontsize', 'color', 'fontfamily', 'weight', 'style'],
  title: ['fontsize', 'color', 'weight', 'style', 'fontfamily'],
  axis_label: ['fontsize', 'color', 'weight', 'style', 'fontfamily'],
  ticks: ['fontsize', 'color', 'direction', 'length', 'width'],
  legend: ['fontsize', 'frameon', 'framealpha', 'edgecolor'],
  line: ['linewidth', 'linestyle', 'marker', 'markersize'],
  errorbar: ['linewidth', 'capsize', 'cap_thickness'],
  bar_series: ['linewidth', 'edgecolor'],
  axes: ['spine_linewidth', 'spine_color'],
  colorbar: ['tick_fontsize', 'outline_width'],
}

/** 信封 → 编辑草稿。内容里的未知字段（`extra`）原样带着走。 */
export function profileToDraft(record: {
  id: string
  display_name: string
  data: Record<string, unknown>
}): StylePreset {
  const d = record.data as unknown as StyleProfileData
  return {
    id: record.id,
    name: record.display_name,
    element: (d.element ?? {}) as StyleProfileData['element'],
    ...(d.palette ? { palette: d.palette } : {}),
    ...(d.annotation ? { annotation: d.annotation } : {}),
    ...(d.subLabel ? { subLabel: d.subLabel } : {}),
    ...(d.page ? { page: d.page } : {}),
    ...(d.background ? { background: d.background } : {}),
    ...(d.derived_from_spec ? { derived_from_spec: d.derived_from_spec } : {}),
    ...(d.extra ? { extra: d.extra } : {}),
  }
}

/** 编辑草稿 → 信封里的 `data`（把 id / name 摘掉，别的原样）。 */
export function draftToData(preset: StylePreset): Record<string, unknown> {
  const { id: _id, name: _name, ...data } = preset
  return data as Record<string, unknown>
}

/** 样式表里角色的显示名；未登记的角色原样显示 */
export const styleRoleLabel = (role: string): string =>
  t(`style.roleLabel.${role}`, { ns: 'dialogs', defaultValue: role })

/** 参与配色循环的系列角色 → 承接颜色的 prop */
const PALETTE_PROP: Record<string, string> = {
  line: 'color',
  scatter: 'facecolor',
  bar_series: 'facecolor',
}

const SUB_LABEL_RE = /^\([a-z]\)$/i

export const isSubLabel = (t: TextObject) => SUB_LABEL_RE.test(t.text.trim())

/* ------------------------------- 提取 ------------------------------------- */

/**
 * 从一个已渲染面板提取样式：每个角色取第一个元素的当前值。
 * 只提取白名单里的 prop，且元素确实暴露了该字段才提取。
 */
export function extractFromManifest(manifest: Manifest): StylePreset['element'] {
  const out: StylePreset['element'] = {}
  for (const [role, props] of Object.entries(STYLE_ROLE_PROPS)) {
    const el = manifest.elements.find((e) => e.role === role && e.editable.length > 0)
    if (!el) continue
    const entry: Record<string, unknown> = {}
    for (const prop of props) {
      const f = el.editable.find((x) => x.prop === prop)
      if (f && f.value !== null && f.value !== undefined) entry[prop] = f.value
    }
    if (Object.keys(entry).length) out[role] = entry
  }
  return out
}

/** 从面板提取系列配色（按 gid 顺序） */
export function extractPalette(manifest: Manifest): string[] {
  const colors: string[] = []
  for (const el of manifest.elements) {
    const prop = PALETTE_PROP[el.role]
    if (!prop) continue
    const f = el.editable.find((x) => x.prop === (el.role === 'line' ? 'color' : 'facecolor'))
    if (typeof f?.value === 'string' && !colors.includes(f.value)) colors.push(f.value)
  }
  return colors.slice(0, 8)
}

/* ------------------------------- 应用 ------------------------------------- */

export type StyleScope = 'panel' | 'selection' | 'sameScript' | 'document'

export const styleScopeLabel = (scope: StyleScope): string =>
  t(`style.scopeLabel.${scope}`, { ns: 'dialogs' })

export interface PanelPlan {
  panel: PanelObject
  patches: PanelOverride[]
  /** 将覆盖的现有 override 数（冲突提示） */
  overwrites: number
  /** 无法映射的项：元素没有该字段（如 3D 刻度无 direction） */
  unmappable: string[]
}

export interface StylePlan {
  panels: PanelPlan[]
  /** 有脚本但还没渲染过，取不到 manifest，无法映射 */
  unrendered: PanelObject[]
  /** 受影响的标注文字对象 id */
  annotationIds: string[]
  subLabelIds: string[]
  page?: { w: number; h: number }
  /** 画布背景（样式里设了才有；`undefined` = 不动背景，与"设成白色"不是一回事） */
  background?: string
}

/** 目标面板集合（scope 语义见 STYLE_SCOPE_LABEL） */
export function targetPanels(
  doc: FigureDocument,
  scope: StyleScope,
  primaryPanelId: string | null,
  selectedIds: string[],
): PanelObject[] {
  const panels = doc.objects.filter(
    (o): o is PanelObject => o.type === 'panel' && !!o.script,
  )
  if (scope === 'document') return panels
  if (scope === 'sameScript') {
    const primary = panels.find((p) => p.id === primaryPanelId)
    if (!primary) return []
    // 同脚本：这里用 fileId 的 stem 前缀不可靠，直接比对 script 字段
    return panels.filter((p) => p.script === primary.script)
  }
  if (scope === 'selection') return panels.filter((p) => selectedIds.includes(p.id))
  const primary = panels.find((p) => p.id === primaryPanelId)
  return primary ? [primary] : []
}

/** 把预设映射成每个面板的 override 批次（不执行，仅供预览与应用） */
export function planStyle(
  preset: StylePreset,
  panels: PanelObject[],
  manifestOf: (panel: PanelObject) => Manifest | null | undefined,
  doc: FigureDocument,
  includeAnnotations: boolean,
): StylePlan {
  const plans: PanelPlan[] = []
  const unrendered: PanelObject[] = []

  for (const panel of panels) {
    const manifest = manifestOf(panel)
    if (!manifest) {
      unrendered.push(panel)
      continue
    }
    const patches: PanelOverride[] = []
    const unmappable: string[] = []
    for (const [role, props] of Object.entries(preset.element)) {
      const els = manifest.elements.filter((e) => e.role === role)
      for (const el of els) {
        for (const [prop, value] of Object.entries(props)) {
          const field = el.editable.find((f) => f.prop === prop)
          if (!field) {
            unmappable.push(
              t('style.unmappableEntry', { ns: 'dialogs', label: el.label, prop }),
            )
            continue
          }
          patches.push({ gid: el.gid, prop, value })
        }
      }
    }
    if (preset.palette?.length) {
      let i = 0
      for (const el of manifest.elements) {
        const prop = PALETTE_PROP[el.role]
        if (!prop) continue
        if (!el.editable.some((f) => f.prop === prop)) continue
        patches.push({ gid: el.gid, prop, value: preset.palette[i % preset.palette.length] })
        i += 1
      }
    }
    const overwrites = patches.filter((p) =>
      panel.overrides.some(
        (o) => o.gid === p.gid && o.prop === p.prop &&
          JSON.stringify(o.value) !== JSON.stringify(p.value),
      ),
    ).length
    plans.push({ panel, patches, overwrites, unmappable })
  }

  const annotationIds: string[] = []
  const subLabelIds: string[] = []
  if (includeAnnotations) {
    for (const o of doc.objects) {
      if (o.type !== 'text') continue
      if (isSubLabel(o)) {
        if (preset.subLabel) subLabelIds.push(o.id)
      } else if (preset.annotation) {
        annotationIds.push(o.id)
      }
    }
  }

  return {
    panels: plans,
    unrendered,
    annotationIds,
    subLabelIds,
    page: preset.page,
    background: preset.background,
  }
}

/** 预设内容的一行行摘要（编辑器里展示 / 删除用） */
export interface PresetEntry {
  role: string
  prop: string
  value: unknown
}

export function presetEntries(preset: StylePreset): PresetEntry[] {
  const out: PresetEntry[] = []
  for (const [role, props] of Object.entries(preset.element)) {
    for (const [prop, value] of Object.entries(props)) out.push({ role, prop, value })
  }
  return out
}
