import type { PanelInfo } from './api'
import { panelRender, type PanelRender } from '@/store/renderStore'
import { formatMessage, msg, type UiMessage } from '@/i18n'
import { PROOF_KIND } from './brand'
import { profileStamp, severityOf, type PublicationProfile, type Severity } from './profile'
import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'
import { panelFullSize } from '@/types/document'
import type { Manifest, ManifestElement } from './api'

/**
 * 出版规范预检 —— **规则全部来自 profile，一条都不在这里硬编码**。
 *
 * 这是同一套规则的第二个求值器：权威规范文件是
 * `src/magplot/profiles/publication.json`（经 `@profiles` 别名 import），
 * Python 侧的求值器是 `src/magplot/engine/preflight.py`（MCP server 走那条）。
 * 浏览器里跑不了 Python，所以求值器必须有两份；两份的判据靠
 * `tests/golden/preflight_vectors.json` 对齐——**pytest 与 vitest 各跑一遍
 * 同一份向量**，改任一侧必须让两边同时绿。
 *
 * 字号一律按**最终物理尺寸**判：manifest 里的 fontsize 是脚本坐标系里的 pt，
 * 面板缩到 60% 摆上版面时，读者量到的是 fontsize × scale。只看原始 fontsize
 * 会让「缩一缩就放行」变成常态，而那正是投稿被拒的头号原因。
 */

export interface PreflightIssue {
  id: string
  severity: Severity
  /**
   * 问题描述的**描述符**，不是翻好的字符串：预检结果会在导出对话框里一直挂着，
   * 中途切语言得跟着换。`id` 才是机器可读的稳定身份——golden vectors 与 proof
   * report 认的都是它，措辞怎么写不影响判据（见 preflight.golden.test.ts）。
   */
  message: UiMessage
  objectIds: string[]
  /** 命中的图内元素 gid（面板级问题为空）——配合元素树一键定位 */
  gids: string[]
  /** 量化细节（等效字号、dpi、比例…）；写进 proof report */
  detail: Record<string, unknown>
}

/** 显示用文本（组件里请配合 useTranslation 订阅语言变化）。 */
export const issueText = (issue: PreflightIssue): string => formatMessage(issue.message)

/** 本文件的文案都在 `errors:preflight.*` 下 */
const pf = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`preflight.${key}`, values, 'errors')

/** 页面/几何比较的容差（mm）。与 Python 的 EPS_MM 同值。 */
const EPS_MM = 0.05

const CJK_RE =
  /[⺀-⻿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]/

/** 带 fontsize 的角色 → text_weight_policy 的键（与 Python 的 _TEXT_ROLES 同源） */
const TEXT_ROLES = new Set([
  'text',
  'title',
  'axis_label',
  'legend_text',
  'ticklabel',
  'ticks',
  'legend',
  'colorbar',
])

const FIT_WORDS = /fit|拟合|regression|回归|trend|linear/i

export const hasCjk = (text: unknown): boolean =>
  typeof text === 'string' && CJK_RE.test(text)

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

const r2 = (v: number | null): number | null => (v == null ? null : Math.round(v * 100) / 100)

/** 数字的紧凑写法（%g 语义）：Python 的 f"{v:g}" 与它同形 */
const g = (v: number): string => String(Math.round(v * 1e6) / 1e6)

function field(el: ManifestElement, prop: string): unknown {
  for (const f of el.editable ?? []) if (f.prop === prop) return f.value
  return undefined
}

/** 元素 gid → 它所属的 axes 前缀（figure 级元素回空串） */
const axesOf = (gid: string): string => (gid.startsWith('axes_') ? gid.split('.')[0] : '')

/* ------------------------ 规范化输入（两侧同源） --------------------------- */

export interface PreflightPanelSpec {
  id: string
  name: string
  kind: 'pdf' | 'raster'
  /** 页面落位 [x, y, w, h]（mm） */
  rect_mm: [number, number, number, number]
  /** 最终尺寸 / 原生尺寸；字号与线宽都乘它 */
  scale: number
  manifest: Manifest | null
  px_w: number | null
  missing: boolean
  stale: boolean
  render_error: string | null
  unapplied_overrides: number
  bitmap_embed: boolean
  hidden: boolean
}

export interface PreflightTextSpec {
  id: string
  text: string
  size_pt: number
  bold: boolean
  rect_mm: [number, number, number, number]
  hidden: boolean
}

export interface PreflightSpec {
  page: { w_mm: number; h_mm: number; margin_mm: number }
  panels: PreflightPanelSpec[]
  texts: PreflightTextSpec[]
  objects: {
    id: string
    type: string
    rect_mm: [number, number, number, number]
    hidden: boolean
  }[]
}

/* ------------------------------- 收集器 ------------------------------------ */

class Sink {
  private items = new Map<string, PreflightIssue>()
  private order: string[] = []
  private profile: PublicationProfile

  constructor(profile: PublicationProfile) {
    this.profile = profile
  }

  add(
    id: string,
    message: UiMessage,
    opts: { objectIds?: string[]; gids?: string[]; detail?: Record<string, unknown> } = {},
  ): void {
    let item = this.items.get(id)
    if (!item) {
      item = { id, severity: severityOf(this.profile, id), message, objectIds: [], gids: [], detail: {} }
      this.items.set(id, item)
      this.order.push(id)
    }
    for (const oid of opts.objectIds ?? []) if (!item.objectIds.includes(oid)) item.objectIds.push(oid)
    for (const gid of opts.gids ?? []) if (!item.gids.includes(gid)) item.gids.push(gid)
    if (opts.detail) item.detail = { ...item.detail, ...opts.detail }
  }

  result(): PreflightIssue[] {
    return this.order.map((k) => this.items.get(k)!)
  }
}

/* -------------------------------- 各组检查 --------------------------------- */

function checkPage(spec: PreflightSpec, profile: PublicationProfile, sink: Sink): void {
  const w = num(spec.page.w_mm) ?? 0
  const h = num(spec.page.h_mm) ?? 0
  const { single, double, tolerance_mm: tol } = profile.widths_mm
  let matched: string | null = null
  for (const [name, target] of [
    ['single', single],
    ['double', double],
  ] as const) {
    if (target != null && Math.abs(w - target) <= tol) {
      matched = name
      break
    }
  }
  if (matched == null && (single != null || double != null)) {
    const want = [single, double].filter((v) => v != null).map(g).join('/')
    sink.add('page-width', pf('pageWidth', { actual: g(w), want }), {
      detail: { page_w_mm: r2(w), single_mm: single, double_mm: double, column: null },
    })
  }

  const ratios = profile.allowed_aspect_ratios ?? []
  if (ratios.length && w > 0 && h > 0) {
    const tolR = profile.aspect_tolerance ?? 0.04
    const actual = w / h
    let best: [string, number] | null = null
    for (const r of ratios) {
      if (!r.w || !r.h) continue
      const rel = Math.abs(actual - r.w / r.h) / (r.w / r.h)
      if (best == null || rel < best[1]) best = [r.id ?? `${g(r.w)}:${g(r.h)}`, rel]
      if (rel <= tolR) {
        best = [r.id ?? '', 0]
        break
      }
    }
    if (best != null && best[1] > tolR) {
      const allowed = ratios.map((r) => r.id).join('、')
      sink.add(
        'page-aspect',
        pf('pageAspect', { ratio: actual.toFixed(3), w: g(w), h: g(h), allowed }),
        { detail: { aspect: r2(actual), closest: best[0] } },
      )
    }
  }
}

function checkPanelState(panel: PreflightPanelSpec, sink: Sink): void {
  const pid = panel.id
  if (panel.missing) {
    sink.add('missing-asset', pf('missingAsset'), {
      objectIds: [pid],
    })
  }
  if (panel.render_error) {
    sink.add('render-error', pf('renderError'), {
      objectIds: [pid],
      detail: { error: String(panel.render_error).slice(0, 200) },
    })
  }
  if (panel.stale) {
    sink.add('stale-render', pf('staleRender'), { objectIds: [pid] })
  }
  const n = panel.unapplied_overrides | 0
  if (n > 0) {
    sink.add('unapplied-override', pf('unappliedOverride', { count: n }), {
      objectIds: [pid],
      detail: { count: n },
    })
  }
  if (panel.bitmap_embed) {
    sink.add('bitmap-embed', pf('bitmapEmbed'), {
      objectIds: [pid],
    })
  }
}

function checkPanelRaster(
  panel: PreflightPanelSpec,
  profile: PublicationProfile,
  sink: Sink,
): void {
  const pid = panel.id
  const wMm = num(panel.rect_mm[2]) ?? 0
  const minDpi = profile.min_raster_dpi ?? 300
  if (panel.px_w && wMm > 0) {
    const dpi = panel.px_w / (wMm / 25.4)
    if (dpi < minDpi) {
      sink.add(
        'raster-dpi',
        pf('rasterDpi', { dpi: dpi.toFixed(0), min: g(minDpi) }),
        { objectIds: [pid], detail: { dpi: r2(dpi), min_dpi: minDpi } },
      )
    }
  }
  // 外部位图内部的文字字号无法可靠判定：如实报 not_verifiable，绝不假装通过
  sink.add(
    'raster-text-not-verifiable',
    pf('rasterTextNotVerifiable'),
    { objectIds: [pid] },
  )
}

function* fontElements(manifest: Manifest): Generator<[ManifestElement, number]> {
  for (const el of manifest.elements ?? []) {
    const size = num(field(el, 'fontsize'))
    if (size != null) yield [el, size]
    const tick = num(field(el, 'tick_fontsize'))
    if (tick != null) yield [el, tick]
    const title = num(field(el, 'title_fontsize'))
    if (title != null) yield [el, title]
  }
}

function checkPanelFonts(
  panel: PreflightPanelSpec,
  profile: PublicationProfile,
  sink: Sink,
): void {
  const manifest = panel.manifest
  if (!manifest) return
  const pid = panel.id
  const scale = num(panel.scale) ?? 1
  const strict = profile.min_effective_font_size_pt
  const floor = profile.absolute_min_font_size_pt
  const biggest = profile.max_font_size_pt
  const fam = profile.font_family
  const accepted = new Set((fam.latin_accepted ?? []).map((s) => s.toLowerCase()))
  const flagged = new Set((fam.latin_substitutes_flagged ?? []).map((s) => s.toLowerCase()))
  const cjk = profile.cjk_fallback
  const cjkOk = new Set((cjk.accepted ?? []).map((s) => s.toLowerCase()))
  const weights = profile.text_weight_policy ?? {}

  for (const [el, size] of fontElements(manifest)) {
    const gid = el.gid
    if (field(el, 'visible') === false) continue
    const eff = size * scale
    if (eff <= floor) {
      sink.add(
        'font-below-absolute-floor',
        pf('fontBelowFloor', { effective: eff.toFixed(2), floor: g(floor) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), floor_pt: floor } },
      )
    } else if (eff < strict) {
      sink.add(
        'font-too-small',
        pf('fontTooSmall', { effective: eff.toFixed(2), min: g(strict) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), min_pt: strict } },
      )
    }
    if (eff > biggest) {
      sink.add(
        'font-too-large',
        pf('fontTooLarge', { effective: eff.toFixed(2), max: g(biggest) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), max_pt: biggest } },
      )
    }
  }

  for (const el of manifest.elements ?? []) {
    const gid = el.gid
    const family = field(el, 'fontfamily')
    const text = field(el, 'text')
    if (typeof family === 'string' && family) {
      const low = family.toLowerCase()
      if (accepted.size && !accepted.has(low)) {
        sink.add(
          'font-family-substituted',
          // 分两条完整句子而不是拼字符串：英文里那半句补语的位置和中文不一样
          flagged.has(low)
            ? pf('fontFamilySubstitutedKnown', { family, want: fam.latin })
            : pf('fontFamilySubstituted', { family, want: fam.latin }),
          { objectIds: [pid], gids: [gid], detail: { family } },
        )
      }
      if (hasCjk(text) && cjk.required && !cjkOk.has(low)) {
        sink.add(
          'cjk-fallback-missing',
          pf('cjkFallbackMissing', { family }),
          { objectIds: [pid], gids: [gid], detail: { family } },
        )
      }
    }
    const role = el.role
    const want = weights[role]
    if ((want === 'bold' || want === 'normal') && TEXT_ROLES.has(role)) {
      const got = field(el, 'weight')
      if (typeof got === 'string' && got && got !== want) {
        sink.add('text-weight-policy', pf('textWeightPolicy', { role, want, got }), {
          objectIds: [pid],
          gids: [gid],
          detail: { role, want, got },
        })
      }
    }
  }
}

function checkPanelAxes(panel: PreflightPanelSpec, profile: PublicationProfile, sink: Sink): void {
  const manifest = panel.manifest
  if (!manifest) return
  const pid = panel.id
  const scale = num(panel.scale) ?? 1
  const axis = profile.axis_policy
  const legend = profile.legend_policy
  const presets = profile.line_widths_pt ?? []
  const tol = profile.line_width_tolerance_pt ?? 0.08
  const wantDir = axis.tick_direction
  const enclosed = !!axis.enclosed_spines
  const maxLabels = axis.max_tick_labels | 0
  const labelRe = axis.label_format_regex ? new RegExp(axis.label_format_regex) : null
  const palette = profile.palette_policy
  const badCmaps = new Set((palette.discouraged_colormaps ?? []).map((c) => c.toLowerCase()))
  const goodCmaps = new Set(
    Object.values(palette.by_semantic ?? {})
      .flat()
      .map((c) => c.toLowerCase()),
  )

  const tickLabels = new Map<string, number>()
  const linesByAxes = new Map<string, ManifestElement[]>()
  const rolesByAxes = new Map<string, Set<string>>()

  for (const el of manifest.elements ?? []) {
    const gid = el.gid
    const role = el.role
    const ax = axesOf(gid)
    if (!rolesByAxes.has(ax)) rolesByAxes.set(ax, new Set())
    rolesByAxes.get(ax)!.add(role)

    if (role === 'legend') {
      if (legend.frame === false && field(el, 'frameon') === true) {
        sink.add('legend-frame', pf('legendFrame'), { objectIds: [pid], gids: [gid] })
      }
      const size = num(field(el, 'fontsize'))
      const lo = num(legend.min_font_size_pt)
      const hi = num(legend.max_font_size_pt)
      if (size != null && lo != null && hi != null) {
        const eff = size * scale
        if (eff < lo - 1e-9 || eff > hi + 1e-9) {
          sink.add(
            'legend-font-size',
            pf('legendFontSize', { effective: eff.toFixed(2), min: g(lo), max: g(hi) }),
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) } },
          )
        }
      }
    }

    if (role === 'ticks') {
      const got = field(el, 'direction')
      if (wantDir && typeof got === 'string' && got !== wantDir) {
        sink.add('tick-direction', pf('tickDirection', { got, want: wantDir }), {
          objectIds: [pid],
          gids: [gid],
          detail: { direction: got, want: wantDir },
        })
      }
    }

    if (role === 'ticklabel') {
      const prefix = gid.slice(0, gid.lastIndexOf('_'))
      tickLabels.set(prefix, (tickLabels.get(prefix) ?? 0) + 1)
    }

    if (role === 'axes') {
      if (enclosed) {
        const off = (['top', 'right', 'bottom', 'left'] as const).filter(
          (s) => field(el, `spine_${s}`) === false,
        )
        if (off.length) {
          sink.add(
            'spines-not-enclosed',
            pf('spinesNotEnclosed', { sides: off.join(', ') }),
            { objectIds: [pid], gids: [gid], detail: { missing: off } },
          )
        }
      }
      const lw = num(field(el, 'spine_linewidth'))
      const frameLw = axis.frame_linewidth_pt ?? []
      if (lw != null && frameLw.length) {
        const eff = lw * scale
        if (frameLw.every((v) => Math.abs(eff - v) > tol)) {
          sink.add(
            'line-width-off-preset',
            pf('frameWidthOffPreset', { effective: eff.toFixed(2), presets: frameLw.map(g).join('/') }),
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) } },
          )
        }
      }
    }

    if (role === 'axis_label' && labelRe) {
      const text = field(el, 'text')
      if (typeof text === 'string' && text.trim() && !labelRe.test(text.trim())) {
        sink.add(
          'axis-label-format',
          pf('axisLabelFormat', { label: text.trim().slice(0, 30), want: axis.label_format }),
          { objectIds: [pid], gids: [gid], detail: { text: text.trim().slice(0, 60) } },
        )
      }
    }

    if (
      ['line', 'scatter', 'fill', 'bar_series', 'bar', 'errorbar', 'arrow_patch'].includes(role)
    ) {
      const lw = num(field(el, 'linewidth'))
      if (lw != null && presets.length && lw > 0) {
        const eff = lw * scale
        if (presets.every((v) => Math.abs(eff - v) > tol)) {
          sink.add(
            'line-width-off-preset',
            pf('lineWidthOffPreset', { effective: eff.toFixed(2), presets: presets.map(g).join('/') }),
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) } },
          )
        }
      }
    }
    if (role === 'line') {
      if (!linesByAxes.has(ax)) linesByAxes.set(ax, [])
      linesByAxes.get(ax)!.push(el)
    }

    if (role === 'image' || role === 'colorbar') {
      const cmap = field(el, 'cmap')
      if (typeof cmap === 'string' && cmap) {
        const low = cmap.toLowerCase()
        if (badCmaps.has(low)) {
          sink.add(
            'discouraged-colormap',
            pf('discouragedColormap', { cmap, recommended: palette.recommended }),
            { objectIds: [pid], gids: [gid], detail: { cmap } },
          )
        } else if (goodCmaps.size && !goodCmaps.has(low)) {
          sink.add(
            'palette-semantic',
            pf('paletteSemantic', { cmap, url: palette.url }),
            { objectIds: [pid], gids: [gid], detail: { cmap } },
          )
        }
      }
    }
  }

  for (const prefix of [...tickLabels.keys()].sort()) {
    const count = tickLabels.get(prefix)!
    if (maxLabels && count > maxLabels) {
      sink.add(
        'tick-label-count',
        pf('tickLabelCount', { axis: prefix, count, max: maxLabels }),
        { objectIds: [pid], gids: [prefix], detail: { count } },
      )
    }
  }

  // --- 数据语义：只给建议，绝不替用户裁决 ---
  for (const ax of [...rolesByAxes.keys()].sort()) {
    const roles = rolesByAxes.get(ax)!
    if (roles.has('bar_series') && !roles.has('errorbar')) {
      sink.add(
        'bar-without-errorbar',
        pf('barWithoutErrorbar'),
        { objectIds: [pid], gids: [ax] },
      )
    }
    const lines = linesByAxes.get(ax) ?? []
    if (lines.some((l) => FIT_WORDS.test(String(field(l, 'label') ?? ''))) && !roles.has('fill')) {
      sink.add(
        'fit-without-ci',
        pf('fitWithoutCi'),
        { objectIds: [pid], gids: [ax] },
      )
    }
    if (
      lines.length >= 2 &&
      lines.every((l) => ['None', 'none', ''].includes(String(field(l, 'marker') ?? 'None')))
    ) {
      sink.add(
        'palette-line-markers',
        pf('paletteLineMarkers', { count: lines.length }),
        { objectIds: [pid], gids: [ax], detail: { lines: lines.length } },
      )
    }
  }
}

function checkGeometry(spec: PreflightSpec, sink: Sink): void {
  const pw = num(spec.page.w_mm) ?? 0
  const ph = num(spec.page.h_mm) ?? 0
  const margin = num(spec.page.margin_mm) ?? 0
  const visible = spec.objects.filter((o) => !o.hidden)

  const out = visible
    .filter(
      (o) =>
        o.rect_mm[0] < -EPS_MM ||
        o.rect_mm[1] < -EPS_MM ||
        o.rect_mm[0] + o.rect_mm[2] > pw + EPS_MM ||
        o.rect_mm[1] + o.rect_mm[3] > ph + EPS_MM,
    )
    .map((o) => o.id)
  if (out.length) {
    sink.add('out-of-page', pf('outOfPage'), { objectIds: out })
  }
  if (margin > 0) {
    const outSet = new Set(out)
    const near = visible
      .filter(
        (o) =>
          !outSet.has(o.id) &&
          (o.rect_mm[0] < margin - EPS_MM ||
            o.rect_mm[1] < margin - EPS_MM ||
            o.rect_mm[0] + o.rect_mm[2] > pw - margin + EPS_MM ||
            o.rect_mm[1] + o.rect_mm[3] > ph - margin + EPS_MM),
      )
      .map((o) => o.id)
    if (near.length) {
      sink.add('outside-margin', pf('outsideMargin', { margin: g(margin) }), { objectIds: near })
    }
  }

  const panels = visible.filter((o) => o.type === 'panel')
  const hit: string[] = []
  for (let a = 0; a < panels.length; a++) {
    for (let b = a + 1; b < panels.length; b++) {
      const ra = panels[a].rect_mm
      const rb = panels[b].rect_mm
      const w = Math.min(ra[0] + ra[2], rb[0] + rb[2]) - Math.max(ra[0], rb[0])
      const h = Math.min(ra[1] + ra[3], rb[1] + rb[3]) - Math.max(ra[1], rb[1])
      if (w > 0 && h > 0 && w * h > 1) {
        for (const o of [panels[a], panels[b]]) if (!hit.includes(o.id)) hit.push(o.id)
      }
    }
  }
  if (hit.length) sink.add('overlap', pf('overlap'), { objectIds: hit })

  const hidden = spec.objects.filter((o) => o.hidden).map((o) => o.id)
  if (hidden.length) sink.add('hidden', pf('hidden'), { objectIds: hidden })
}

function checkTexts(spec: PreflightSpec, profile: PublicationProfile, sink: Sink): void {
  const strict = profile.min_effective_font_size_pt
  const floor = profile.absolute_min_font_size_pt
  const cjk = profile.cjk_fallback
  for (const t of spec.texts) {
    if (t.hidden) continue
    const size = num(t.size_pt) ?? 0
    // 画布标注的 size_pt 已经是页面上的绝对 pt：不再乘 scale
    if (size <= floor) {
      sink.add('font-below-absolute-floor', pf('textBelowFloor', { size: g(size), floor: g(floor) }), {
        objectIds: [t.id],
        detail: { effective_pt: r2(size), floor_pt: floor },
      })
    } else if (size < strict) {
      sink.add('font-too-small', pf('textTooSmall', { size: g(size), min: g(strict) }), {
        objectIds: [t.id],
        detail: { effective_pt: r2(size), min_pt: strict },
      })
    }
    if (hasCjk(t.text) && cjk.required && !(cjk.accepted ?? []).length) {
      sink.add('cjk-fallback-missing', pf('textCjkFallbackMissing'), {
        objectIds: [t.id],
      })
    }
  }
}

function checkMissingManifest(panel: PreflightPanelSpec, sink: Sink): void {
  sink.add(
    'panel-text-not-verifiable',
    pf('panelTextNotVerifiable'),
    { objectIds: [panel.id] },
  )
}

/** 跑一遍预检（规范化输入 → 问题清单）。与 Python 的 `preflight.run` 一一对应。 */
export function runSpec(spec: PreflightSpec, profile: PublicationProfile): PreflightIssue[] {
  const sink = new Sink(profile)
  checkPage(spec, profile, sink)
  for (const panel of spec.panels) {
    if (panel.hidden) continue
    checkPanelState(panel, sink)
    if (panel.kind === 'raster') checkPanelRaster(panel, profile, sink)
    else if (!panel.manifest) checkMissingManifest(panel, sink)
    else {
      checkPanelFonts(panel, profile, sink)
      checkPanelAxes(panel, profile, sink)
    }
  }
  checkTexts(spec, profile, sink)
  checkGeometry(spec, sink)
  return sink.result()
}

export interface PreflightSummary {
  errors: PreflightIssue[]
  warnings: PreflightIssue[]
  notVerifiable: PreflightIssue[]
  suggestions: PreflightIssue[]
  counts: Record<Severity, number>
  /** error 非空 = 默认阻止导出（用户可显式确认后强制导出） */
  blocking: boolean
}

export function summarize(issues: PreflightIssue[]): PreflightSummary {
  const pick = (s: Severity) => issues.filter((i) => i.severity === s)
  const errors = pick('error')
  const warnings = pick('warn')
  const notVerifiable = pick('not_verifiable')
  const suggestions = pick('suggestion')
  return {
    errors,
    warnings,
    notVerifiable,
    suggestions,
    counts: {
      error: errors.length,
      warn: warnings.length,
      not_verifiable: notVerifiable.length,
      suggestion: suggestions.length,
    },
    blocking: errors.length > 0,
  }
}

/* ------------------- 画布文档 → 规范化输入（前端专用） --------------------- */

/**
 * 面板的最终缩放比 = 摆放宽度 / 原生宽度。字号与线宽都按它折算成读者量到的 pt。
 * 旧实现只用一个 9pt 的估计值（`effectivePt`），只能给出「大概偏小」；
 * 有 manifest 之后每个 artist 的真实字号都在手里，判据从估计变成事实。
 */
export function panelScale(o: PanelObject): number {
  const full = panelFullSize(o)
  if (!o.nativeW) return 1
  return full.w / o.nativeW
}

export function buildSpec(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  render: { byKey: Record<string, PanelRender>; latest: Record<string, string> },
): PreflightSpec {
  const rect = (o: CanvasObject): [number, number, number, number] => [o.x, o.y, o.w, o.h]
  const panels: PreflightPanelSpec[] = doc.objects
    .filter((o): o is PanelObject => o.type === 'panel')
    .map((o) => {
      const r = panelRender(render, o)
      return {
        id: o.id,
        name: o.name ?? o.fileId,
        kind: o.fileKind,
        rect_mm: rect(o),
        scale: panelScale(o),
        manifest: r?.manifest ?? null,
        px_w: o.pxW ?? null,
        missing: !assets[o.fileId],
        stale: !!r?.stale,
        render_error: o.overrides.length > 0 && r?.status === 'error' ? (r.error ?? 'error') : null,
        // 有 override 但引擎那边还没画出这一版：成图会与画布不一致
        unapplied_overrides:
          o.overrides.length > 0 && r?.lastPatches !== JSON.stringify(o.overrides)
            ? o.overrides.length
            : 0,
        bitmap_embed: !!(o.flipH || o.flipV || (o.opacity != null && o.opacity < 1)),
        hidden: !!o.hidden,
      }
    })
  return {
    page: { w_mm: doc.page.w, h_mm: doc.page.h, margin_mm: doc.page.margin ?? 0 },
    panels,
    texts: doc.objects
      .filter((o) => o.type === 'text')
      .map((o) => ({
        id: o.id,
        text: o.type === 'text' ? o.text : '',
        size_pt: o.type === 'text' ? o.sizePt : 0,
        bold: o.type === 'text' ? o.bold : false,
        rect_mm: rect(o),
        hidden: !!o.hidden,
      })),
    objects: doc.objects.map((o) => ({
      id: o.id,
      type: o.type,
      rect_mm: rect(o),
      hidden: !!o.hidden,
    })),
  }
}

/**
 * 导出前检查：把「导出去了才发现」的问题在点导出前列出来。
 * 全部纯计算——doc + 素材表 + 渲染状态 + profile 进，问题清单出；
 * 每条带 objectIds，配合 revealObjects 一键定位。
 */
export function runPreflight(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  /** 渲染态按「文件 + 变体」分键：取某个面板的那一份必须带上面板本身 */
  render: { byKey: Record<string, PanelRender>; latest: Record<string, string> },
  profile: PublicationProfile,
): PreflightIssue[] {
  return runSpec(buildSpec(doc, assets, render), profile)
}

/** proof report 的载荷（随导出落盘，作为投稿留档） */
export function buildProofPayload(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  issues: PreflightIssue[],
  settings: { dpi: number; formats: string[]; stem: string },
  profile: PublicationProfile,
  /** 有 error 却仍然导出时，用户按下的那次显式确认 */
  forced?: { forced: boolean; acknowledged: string[] },
) {
  const sum = summarize(issues)
  return {
    kind: PROOF_KIND,
    version: 2,
    stem: settings.stem,
    profile: profileStamp(profile),
    page_mm: { w: doc.page.w, h: doc.page.h, margin: doc.page.margin ?? 0 },
    dpi: settings.dpi,
    formats: settings.formats,
    checks: issues.map((i) => ({
      id: i.id,
      severity: i.severity,
      // 留档写的是**当前语言的成文**（人要读），机器可读的身份是 id。
      text: issueText(i),
      count: i.objectIds.length,
      object_ids: i.objectIds,
      gids: i.gids,
      detail: i.detail,
    })),
    check_counts: sum.counts,
    // not_verifiable 单独留档：那是「我们查不了，人得自己看」，不能和 warn 混在一起
    not_verifiable: sum.notVerifiable.map((i) => ({
      id: i.id,
      text: issueText(i),
      object_ids: i.objectIds,
    })),
    forced: forced?.forced ?? false,
    acknowledged: forced?.acknowledged ?? [],
    objects: doc.objects
      .filter((o) => !o.hidden)
      .map((o) =>
        o.type === 'panel'
          ? {
              type: o.type,
              name: o.name ?? o.fileId,
              file: o.fileId,
              mtime: assets[o.fileId]?.mtime ?? null,
              script: o.script ?? null,
              overrides: o.overrides.length,
              rect_mm: [o.x, o.y, o.w, o.h].map((v) => Math.round(v * 100) / 100),
            }
          : {
              type: o.type,
              name: undefined,
              rect_mm: [o.x, o.y, o.w, o.h].map((v) => Math.round(v * 100) / 100),
            },
      ),
  }
}
