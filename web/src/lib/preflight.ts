import type { PanelInfo } from './api'
import { panelRender, type PanelRender } from '@/store/renderStore'
import { formatMessage, msg, type UiMessage } from '@/i18n'
import { PROOF_KIND } from './brand'
import {
  FALLBACK_MIN_FONT_SIZE_PT,
  profileStamp,
  severityOf,
  type PublicationProfile,
  type Severity,
} from './profile'
import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'
import { panelFullSize } from '@/types/document'
import type { Manifest, ManifestElement } from './api'

/**
 * 出版规范预检 —— **规则全部来自 profile，一条都不在这里硬编码**。
 *
 * 这是同一套规则的第二个求值器：权威规范文件是
 * `src/tavotto/profiles/publication.json`（经 `@profiles` 别名 import），
 * Python 侧的求值器是 `src/tavotto/engine/preflight.py`（MCP server 走那条）。
 * 浏览器里跑不了 Python，所以求值器必须有两份；两份的判据靠
 * `tests/golden/preflight_vectors.json` 对齐——**pytest 与 vitest 各跑一遍
 * 同一份向量**，改任一侧必须让两边同时绿。
 *
 * 字号一律按**最终物理尺寸**判：manifest 里的 fontsize 是脚本坐标系里的 pt，
 * 面板缩到 60% 摆上版面时，读者量到的是 fontsize × scale。只看原始 fontsize
 * 会让「缩一缩就放行」变成常态，而那正是投稿被拒的头号原因。
 */

/**
 * 一次**具体命中**：某个对象 / 图内元素上的这一条规则，带它自己那份量化细节。
 *
 * 聚合项（`PreflightIssue`）回答「这份文档有没有过」，命中回答「是谁没过」。
 * 问题面板要的是后者：一行一个真实对象、一句它自己的当前值，点一下就能落到
 * 那个字段上。聚合项做不到——它的 `detail` 属于**最糟的那一次**，拿它去描述
 * 另外两个元素等于说了三遍同一个数字，其中两遍是假的。
 *
 * **不进跨语言合同。** `tests/golden/preflight_vectors.json` 比的是聚合投影
 * （id / severity / message / object_ids / gids / detail），Python 侧那份求值器
 * 服务的是 MCP 的聚合清单，不需要这一层。看护用例盯着两者的一致性：命中的
 * objectId / gid 并起来必须与聚合项逐字相等。
 */
export interface PreflightOccurrence {
  objectId: string | null
  /** 图内元素 gid；面板级 / 页面级命中为 null */
  gid: string | null
  /** 命中的那个属性（`fontsize` / `linewidth` / `sizePt`…）；说不出来时为 null */
  prop: string | null
  /** 这一次命中自己的文案（聚合项只保留最糟那次的） */
  message: UiMessage
  /** 这一次命中自己的量化细节 */
  detail: Record<string, unknown>
}

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
  /** 逐条命中（问题面板用）。聚合投影不含它——见 `PreflightOccurrence` */
  occurrences: PreflightOccurrence[]
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

/**
 * 一条检查项一个条目，命中的对象累积进 objectIds/gids。
 *
 * **文案与 detail 必须来自同一次命中。** 旧实现保留第一条命中的文案，却让
 * detail 被最后一条无条件覆盖：8.1pt 与 8.4pt 两个字号先后命中
 * `font-too-small`，文案说 8.1，`effective_pt` 却是 8.4——导出对话框据此把
 * 8.4 显示成「最小字号」，写进 proof 的那份留档自相矛盾。
 *
 * 规则（`engine/preflight.py` 的 `_Sink` 逐条同源）：
 * * 命中带 `worse`（越大越糟的量化排名）时，**最糟的那次**同时决定文案与
 *   detail，其余的只贡献 objectIds/gids；
 * * 不带 `worse` 的（family / cmap / 文本这类没法比大小的），**第一次命中
 *   说了算**——与保留第一条文案的做法一致，至少永远自洽。
 */
class Sink {
  private items = new Map<string, PreflightIssue>()
  private worst = new Map<string, number>()
  private order: string[] = []
  private profile: PublicationProfile
  /** 每条规则下按「对象 + 元素」去重的命中排名（与聚合项同一把尺子） */
  private hitWorst = new Map<string, number>()

  constructor(profile: PublicationProfile) {
    this.profile = profile
  }

  add(
    id: string,
    message: UiMessage,
    opts: {
      objectIds?: string[]
      gids?: string[]
      detail?: Record<string, unknown>
      worse?: number
      /** 命中的属性名（`fontsize` / `linewidth`…）——问题面板据此定位字段 */
      prop?: string
    } = {},
  ): void {
    let item = this.items.get(id)
    let fresh = false
    if (!item) {
      item = {
        id,
        severity: severityOf(this.profile, id),
        message,
        objectIds: [],
        gids: [],
        detail: {},
        occurrences: [],
      }
      this.items.set(id, item)
      this.order.push(id)
      fresh = true
    }
    for (const oid of opts.objectIds ?? []) if (!item.objectIds.includes(oid)) item.objectIds.push(oid)
    for (const gid of opts.gids ?? []) if (!item.gids.includes(gid)) item.gids.push(gid)

    this.record(item, message, opts)

    const prev = this.worst.get(id)
    const ranked = opts.worse != null
    const wins = fresh || (ranked && (prev == null || opts.worse! > prev))
    if (ranked && (prev == null || opts.worse! > prev)) this.worst.set(id, opts.worse!)
    if (!wins) return
    item.message = message
    if (opts.detail) item.detail = { ...item.detail, ...opts.detail }
  }

  /**
   * 逐条命中入账。**同一个「规则 + 对象 + 元素」只留一条**——去重的尺子与
   * 聚合项完全一样（带 `worse` 的取最糟那次，不带的第一次说了算），否则同一
   * 个元素的三个字号字段会在面板上排成三行说同一件事。
   *
   * 一次调用可能带来多个命中：`out-of-page` 一次交上来一串对象 id，
   * 字号那类一次一个 gid。展开规则固定——有 gid 就按 gid 展开，
   * 否则按对象 id 展开，两者都没有就是页面级的一条。
   */
  private record(
    item: PreflightIssue,
    message: UiMessage,
    opts: { objectIds?: string[]; gids?: string[]; detail?: Record<string, unknown>; worse?: number; prop?: string },
  ): void {
    const detail = opts.detail ?? {}
    const prop = opts.prop ?? null
    const objectId = opts.objectIds?.[0] ?? null
    const pairs: [string | null, string | null][] = (opts.gids ?? []).length
      ? opts.gids!.map((gid) => [objectId, gid])
      : (opts.objectIds ?? []).length
        ? opts.objectIds!.map((oid) => [oid, null])
        : [[null, null]]
    for (const [oid, gid] of pairs) {
      const key = `${item.id}\u0000${oid ?? ''}\u0000${gid ?? ''}\u0000${prop ?? ''}`
      const idx = item.occurrences.findIndex(
        (o) => o.objectId === oid && o.gid === gid && o.prop === prop,
      )
      if (idx < 0) {
        if (opts.worse != null) this.hitWorst.set(key, opts.worse)
        item.occurrences.push({ objectId: oid, gid, prop, message, detail })
        continue
      }
      // 已经有一条：只有「带排名且更糟」才顶掉它（与聚合项同一条规则）
      if (opts.worse == null) continue
      const prev = this.hitWorst.get(key)
      if (prev != null && opts.worse <= prev) continue
      this.hitWorst.set(key, opts.worse)
      item.occurrences[idx] = { objectId: oid, gid, prop, message, detail }
    }
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
      prop: 'page.w',
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
      worse: n,
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
        { objectIds: [pid], detail: { dpi: r2(dpi), min_dpi: minDpi }, worse: -dpi },
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

function* fontElements(
  manifest: Manifest,
): Generator<[ManifestElement, number, string]> {
  for (const el of manifest.elements ?? []) {
    const size = num(field(el, 'fontsize'))
    if (size != null) yield [el, size, 'fontsize']
    const tick = num(field(el, 'tick_fontsize'))
    if (tick != null) yield [el, tick, 'tick_fontsize']
    const title = num(field(el, 'title_fontsize'))
    if (title != null) yield [el, title, 'title_fontsize']
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
  const strict = num(profile.min_effective_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const floor = num(profile.absolute_min_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const biggest = profile.max_font_size_pt
  const fam = profile.font_family
  const accepted = new Set((fam.latin_accepted ?? []).map((s) => s.toLowerCase()))
  const flagged = new Set((fam.latin_substitutes_flagged ?? []).map((s) => s.toLowerCase()))
  const cjk = profile.cjk_fallback
  const cjkOk = new Set((cjk.accepted ?? []).map((s) => s.toLowerCase()))
  const weights = profile.text_weight_policy ?? {}

  for (const [el, size, sizeProp] of fontElements(manifest)) {
    const gid = el.gid
    if (field(el, 'visible') === false) continue
    const eff = size * scale
    if (eff <= floor) {
      sink.add(
        'font-below-absolute-floor',
        pf('fontBelowFloor', { effective: eff.toFixed(2), floor: g(floor) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), floor_pt: floor }, worse: -eff, prop: sizeProp },
      )
    } else if (eff < strict) {
      sink.add(
        'font-too-small',
        pf('fontTooSmall', { effective: eff.toFixed(2), min: g(strict) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), min_pt: strict }, worse: -eff, prop: sizeProp },
      )
    }
    if (eff > biggest) {
      sink.add(
        'font-too-large',
        pf('fontTooLarge', { effective: eff.toFixed(2), max: g(biggest) }),
        { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff), max_pt: biggest }, worse: eff, prop: sizeProp },
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
          { objectIds: [pid], gids: [gid], detail: { family }, prop: 'fontfamily' },
        )
      }
      if (hasCjk(text) && cjk.required && !cjkOk.has(low)) {
        sink.add(
          'cjk-fallback-missing',
          pf('cjkFallbackMissing', { family }),
          { objectIds: [pid], gids: [gid], detail: { family }, prop: 'fontfamily' },
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
          prop: 'weight',
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
        sink.add('legend-frame', pf('legendFrame'), { objectIds: [pid], gids: [gid], prop: 'frameon' })
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
            // 越出区间越远越糟（两边都可能出界，所以取到区间的距离）
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) },
              worse: Math.max(lo - eff, eff - hi), prop: 'fontsize' },
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
          prop: 'direction',
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
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) },
              worse: Math.min(...frameLw.map((v) => Math.abs(eff - v))), prop: 'spine_linewidth' },
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
          { objectIds: [pid], gids: [gid], detail: { text: text.trim().slice(0, 60) }, prop: 'text' },
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
            { objectIds: [pid], gids: [gid], detail: { effective_pt: r2(eff) },
              worse: Math.min(...presets.map((v) => Math.abs(eff - v))), prop: 'linewidth' },
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
            { objectIds: [pid], gids: [gid], detail: { cmap }, prop: 'cmap' },
          )
        } else if (goodCmaps.size && !goodCmaps.has(low)) {
          sink.add(
            'palette-semantic',
            pf('paletteSemantic', { cmap, url: palette.url }),
            { objectIds: [pid], gids: [gid], detail: { cmap }, prop: 'cmap' },
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
        { objectIds: [pid], gids: [prefix], detail: { count }, worse: count },
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
        { objectIds: [pid], gids: [ax], detail: { lines: lines.length }, worse: lines.length },
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
  const strict = num(profile.min_effective_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const floor = num(profile.absolute_min_font_size_pt) ?? FALLBACK_MIN_FONT_SIZE_PT
  const cjk = profile.cjk_fallback
  for (const t of spec.texts) {
    if (t.hidden) continue
    const size = num(t.size_pt) ?? 0
    // 画布标注的 size_pt 已经是页面上的绝对 pt：不再乘 scale
    if (size <= floor) {
      sink.add('font-below-absolute-floor', pf('textBelowFloor', { size: g(size), floor: g(floor) }), {
        objectIds: [t.id],
        detail: { effective_pt: r2(size), floor_pt: floor },
        worse: -size,
        prop: 'sizePt',
      })
    } else if (size < strict) {
      sink.add('font-too-small', pf('textTooSmall', { size: g(size), min: g(strict) }), {
        objectIds: [t.id],
        detail: { effective_pt: r2(size), min_pt: strict },
        worse: -size,
        prop: 'sizePt',
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
  /**
   * 对象在页面上**真正占的**轴对齐包围盒。
   *
   * `x/y/w/h` 是**未旋转**的框；文字/箭头/形状可以带任意角度 `rotationDeg`
   * （绕包围盒中心，与导出时 `pymupdf.Matrix(deg)` 的 morph 同一约定）。
   * 直接拿未旋转的框去判出血、页边距与重叠，一条细长标注贴着页边旋转 45°
   * 时会「通过」预检，导出的几何却已经被裁掉；重叠也会同时出现漏报与误报。
   * 面板不带 `rotationDeg`（它用的是 90° 档的 `rotation`，w/h 已经互换过），
   * 所以这里只对有这个字段的对象生效。
   */
  const rect = (o: CanvasObject): [number, number, number, number] => {
    const deg = (o as { rotationDeg?: number }).rotationDeg ?? 0
    if (!deg) return [o.x, o.y, o.w, o.h]
    const rad = (deg * Math.PI) / 180
    const c = Math.abs(Math.cos(rad))
    const sn = Math.abs(Math.sin(rad))
    const w = o.w * c + o.h * sn
    const h = o.w * sn + o.h * c
    return [o.x + (o.w - w) / 2, o.y + (o.h - h) / 2, w, h]
  }
  const panels: PreflightPanelSpec[] = doc.objects
    .filter((o): o is PanelObject => o.type === 'panel')
    .map((o) => {
      const r = panelRender(render, o)
      return {
        id: o.id,
        name: o.name ?? o.fileId,
        // 预检的 kind 只区分「矢量 / 位图」：runtime 面板由引擎出矢量 SVG，
        // 按 pdf 档判（golden vectors 的枚举不为它扩张——语义上就是矢量）
        kind: o.fileKind === 'raster' ? ('raster' as const) : ('pdf' as const),
        rect_mm: rect(o),
        scale: panelScale(o),
        manifest: r?.manifest ?? null,
        px_w: o.pxW ?? null,
        // runtime 素材不在磁盘素材表里，这不是「缺失」——它的可用性由
        // stale/rerun 那套状态表达
        missing: o.fileKind === 'runtime' ? false : !assets[o.fileId],
        stale: !!r?.stale,
        render_error: o.overrides.length > 0 && r?.status === 'error'
          ? (formatMessage(r.error) || 'error')
          : null,
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
