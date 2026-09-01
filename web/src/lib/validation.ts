/**
 * 统一检查服务（ADR 0030）。**全产品只有这一处回答「这份项目有什么问题」。**
 *
 * ```text
 * Style   图长什么样      Spec  图要满足什么      Export  文件怎么生成
 *                          └── Validation 只读 Spec 求值，不改图
 * ```
 *
 * 与 `lib/preflight.ts` 的分工是**求值 vs. 导航**：
 *
 * * `preflight.ts` 是**规则求值器**（两份，Python 侧那份服务 MCP，
 *   靠 `tests/golden/preflight_vectors.json` 对齐）。它回答「过没过」。
 * * 本文件把求值结果接成**能定位的问题**：补上画布维度、把聚合项摊成逐条
 *   命中、认出该跳到哪个对象/哪个字段、算出稳定指纹。它回答「谁没过、
 *   点一下去哪」。
 *
 * **阈值一个字都不在这里。** 规范来自 `lib/specBinding.resolveDocumentSpec()`
 * ——「这个项目按哪套规范检查」的唯一判据（ADR 0029）。
 *
 * ### 三类规则（不混在一起）
 *
 * | 类 | 什么时候能判 | 谁产生 |
 * | --- | --- | --- |
 * | Document / Object | 编辑时实时可见 | `preflight.runSpec()` |
 * | Export context | 选了格式与 PPI 之后 | 本文件的 `exportContextIssues()` |
 * | Readiness | 项目接入事实 | `engine/readiness.py`（**不进这里**，面板上另给一条出口） |
 *
 * Readiness 刻意不产生 issue：「这张图还没连上脚本」与「这张图字号偏小」是
 * 两件不同的事，混进同一个清单之后用户既分不清轻重，也找不到各自的下一步。
 */
import { formatMessage, msg, type UiMessage } from '@/i18n'
import type { PanelInfo } from './api'
import type { PanelRender } from '@/store/renderStore'
import { fixOptions, planFix } from './issueFix'
import { severityOf, type PublicationProfile, type Severity } from './profile'
import {
  buildSpec,
  runSpec,
  type PreflightIssue,
  type PreflightOccurrence,
} from './preflight'
import type { CanvasData, CanvasObject, FigureDocument } from '@/types/document'

/* ------------------------------ Issue 模型 -------------------------------- */

/**
 * 稳定对象引用。**四个维度缺一不可**——`canvasId` 正是改造前缺的那一维：
 * 多画布项目里「那个对象」必须说得出在哪张画布上，否则定位只能在当前画布
 * 里瞎找，找不到就静默什么都不做。
 */
export interface ObjectRef {
  documentId: string
  canvasId: string
  /** 画布对象 id（页面级问题为 null） */
  objectId: string | null
  /** 图内元素 gid（面板级 / 页面级问题为 null）。**绝不进用户可见文案** */
  gid: string | null
}

/** 问题指向的东西是什么——界面据此说人话，不读 gid。 */
export interface IssueSubject {
  kind: 'page' | 'object' | 'element'
  objectType?: CanvasObject['type']
  /** 用户看得懂的对象名（面板名 / 文字前几个字）；没有就 null */
  objectName?: string
  /** 引擎给的元素标签（中文散文），渲染时过 `engineLabel()` 换成界面语言 */
  elementLabel?: string
  elementRole?: string
}

export type FixKind = 'none' | 'safe_auto' | 'user_choice'

/** 规则属于哪一类上下文。 */
export type IssueContext = 'document' | 'export'

export interface ValidationIssue {
  /** = `fingerprint`。无变化时逐字稳定，UI 拿它当 key，值变了也不闪 */
  issueId: string
  ruleCode: string
  severity: Severity
  context: IssueContext
  objectRef: ObjectRef
  subject: IssueSubject
  /** 命中的属性（`fontsize` / `sizePt` / `page.w`…）；说不出来时 null */
  propertyPath: string | null
  /** 完整成文的描述符（**不是翻好的字符串**：切语言要跟着换） */
  message: UiMessage
  /** 量化细节（结构化、可安全展示）；技术详情默认折叠 */
  technicalDetails: Record<string, unknown>
  fixKind: FixKind
}

/**
 * 指纹 = 规则 + 对象 + 元素 + 属性。**刻意不含当前值**：7.5pt 改成 7.6pt
 * 仍然是同一条问题，指纹跟着值走的话每敲一个数字整行都会被 React 当成新
 * 节点重建——焦点掉、动效重播、展开状态丢。
 */
export function fingerprintOf(
  ruleCode: string,
  ref: ObjectRef,
  propertyPath: string | null,
): string {
  return [ruleCode, ref.canvasId, ref.objectId ?? '', ref.gid ?? '', propertyPath ?? ''].join('|')
}

/* ------------------------------ 规则目录 ---------------------------------- */

/**
 * 每条规则的**导航属性**：属于哪类上下文、有没有确定安全的修复。
 *
 * `fix` 是**规则的意图**，不是这一条命中的结论——真正能不能修由
 * `issueFix.planFix()` 用当前值与当前规范算，算不出来就降回 `none`。
 * 两层都要有：只有目录的话会给出一颗按了没反应的「修复」按钮，只有
 * planFix 的话每次渲染都要为每条问题算一遍计划。
 */
const RULES: Record<string, { context: IssueContext; fix: FixKind }> = {
  'page-width': { context: 'document', fix: 'user_choice' },
  'page-aspect': { context: 'document', fix: 'none' },
  'missing-asset': { context: 'document', fix: 'none' },
  'render-error': { context: 'document', fix: 'none' },
  'stale-render': { context: 'document', fix: 'none' },
  'unapplied-override': { context: 'document', fix: 'none' },
  'bitmap-embed': { context: 'document', fix: 'none' },
  'raster-dpi': { context: 'document', fix: 'none' },
  'raster-text-not-verifiable': { context: 'document', fix: 'none' },
  'panel-text-not-verifiable': { context: 'document', fix: 'none' },
  'font-below-absolute-floor': { context: 'document', fix: 'safe_auto' },
  'font-too-small': { context: 'document', fix: 'safe_auto' },
  'font-too-large': { context: 'document', fix: 'safe_auto' },
  'font-family-substituted': { context: 'document', fix: 'none' },
  'cjk-fallback-missing': { context: 'document', fix: 'none' },
  // 两条都 `fix: 'none'`：能修的动作是「换一个画得出这些字的字体」，
  // 而换哪一个只有用户说得出（自动挑一个会让同一份文档在两台机器上不一样）。
  'glyph-missing': { context: 'document', fix: 'none' },
  'glyph-substituted': { context: 'document', fix: 'none' },
  'text-weight-policy': { context: 'document', fix: 'safe_auto' },
  'legend-frame': { context: 'document', fix: 'safe_auto' },
  'legend-font-size': { context: 'document', fix: 'safe_auto' },
  'tick-direction': { context: 'document', fix: 'safe_auto' },
  'spines-not-enclosed': { context: 'document', fix: 'safe_auto' },
  'line-width-off-preset': { context: 'document', fix: 'safe_auto' },
  'axis-label-format': { context: 'document', fix: 'none' },
  'discouraged-colormap': { context: 'document', fix: 'none' },
  'palette-semantic': { context: 'document', fix: 'none' },
  'tick-label-count': { context: 'document', fix: 'none' },
  'bar-without-errorbar': { context: 'document', fix: 'none' },
  'fit-without-ci': { context: 'document', fix: 'none' },
  'palette-line-markers': { context: 'document', fix: 'none' },
  'out-of-page': { context: 'document', fix: 'none' },
  'outside-margin': { context: 'document', fix: 'none' },
  'overlap': { context: 'document', fix: 'none' },
  'hidden': { context: 'document', fix: 'none' },
}

/** 规则目录里没登记的 code：按 document / 不可自动修复处理，绝不猜。 */
const UNKNOWN_RULE = { context: 'document' as IssueContext, fix: 'none' as FixKind }

export const ruleEntry = (code: string) => RULES[code] ?? UNKNOWN_RULE

/** 目录里登记过的全部 rule code（看护用例拿它与求值器对拍）。 */
export const knownRuleCodes = (): string[] => Object.keys(RULES).sort()

/* ---------------------------- 求值 → 可定位问题 ---------------------------- */

export interface CanvasInput {
  canvasId: string
  canvasName: string
  doc: FigureDocument
  profile: PublicationProfile
}

export interface ValidateInput {
  documentId: string
  canvases: CanvasInput[]
  assets: Record<string, PanelInfo>
  render: { byKey: Record<string, PanelRender>; latest: Record<string, string> }
}

/** 一次检查的结果。**按画布分片存**，改一张画布只重算那一片。 */
export interface CanvasResult {
  canvasId: string
  canvasName: string
  issues: ValidationIssue[]
  /**
   * 求值器的**聚合投影**（`PreflightIssue`），原样留着。
   *
   * proof report 与 MCP 那条入口认的是这个形状（id / severity / object_ids /
   * gids / detail），而它是**同一次求值**的产物——导出对话框据此不必再跑第二
   * 遍求值器（ADR 0030 §导出）。两份投影，一次计算。
   */
  raw: PreflightIssue[]
}

/**
 * 找出这条命中指向的对象，给出人话主语。
 *
 * 元素名取 manifest 里引擎给的 `label`（「X 轴标题」「图例」这类），
 * **不是 gid**；取不到就退到角色名，再取不到就只说面板名。
 */
function subjectOf(
  doc: FigureDocument,
  occ: PreflightOccurrence,
  manifestOf: (objectId: string) => { elements?: { gid: string; role: string; label: string }[] } | null,
): IssueSubject {
  if (!occ.objectId) return { kind: 'page' }
  const obj = doc.objects.find((o) => o.id === occ.objectId)
  const objectName =
    obj?.type === 'panel'
      ? (obj.name ?? obj.fileId)
      : obj?.type === 'text'
        ? obj.text.trim().slice(0, 24) || undefined
        : undefined
  if (!occ.gid) {
    return { kind: 'object', objectType: obj?.type, objectName }
  }
  const el = manifestOf(occ.objectId)?.elements?.find((e) => e.gid === occ.gid)
  return {
    kind: 'element',
    objectType: obj?.type,
    objectName,
    elementLabel: el?.label,
    elementRole: el?.role,
  }
}

/**
 * 一张画布 → 可定位问题清单。
 *
 * 聚合项摊成逐条命中：一行一个真实对象，各带自己那句话与自己那份细节。
 * 摊开而不是照搬聚合项，是因为聚合项的 `detail` 属于**最糟的那一次**——
 * 拿它去描述另外两个元素，说出来的数字对其中两个是假的。
 */
export function validateCanvas(
  input: CanvasInput,
  documentId: string,
  assets: Record<string, PanelInfo>,
  render: { byKey: Record<string, PanelRender>; latest: Record<string, string> },
): CanvasResult {
  const { doc, canvasId, canvasName, profile } = input
  const spec = buildSpec(doc, assets, render)
  const raw = runSpec(spec, profile)
  const manifests = new Map(spec.panels.map((p) => [p.id, p.manifest]))
  const manifestOf = (objectId: string) => manifests.get(objectId) ?? null
  const issues: ValidationIssue[] = []
  for (const item of raw) {
    for (const occ of item.occurrences) {
      issues.push(
        buildIssue(
          item,
          occ,
          { documentId, canvasId, objectId: occ.objectId, gid: occ.gid },
          subjectOf(doc, occ, manifestOf),
          profile,
          doc,
        ),
      )
    }
  }
  return { canvasId, canvasName, issues: dedupe(issues), raw }
}

function buildIssue(
  item: PreflightIssue,
  occ: PreflightOccurrence,
  objectRef: ObjectRef,
  subject: IssueSubject,
  profile: PublicationProfile,
  doc: FigureDocument,
): ValidationIssue {
  const entry = ruleEntry(item.id)
  const base: ValidationIssue = {
    issueId: fingerprintOf(item.id, objectRef, occ.prop),
    ruleCode: item.id,
    severity: item.severity,
    context: entry.context,
    objectRef,
    subject,
    propertyPath: occ.prop,
    message: occ.message,
    technicalDetails: occ.detail,
    fixKind: 'none',
  }
  // 目录说这条**可能**能修时才去算计划；算不出来就老实回 none——
  // 一颗按了没反应的「修复」按钮比没有按钮更坏
  // `user_choice` 的计划要等用户挑完才算得出来（`planFix` 拿不到 choice 会
  // 回 null），所以那一档只看目录有没有给出选项
  if (entry.fix === 'safe_auto') base.fixKind = planFix(base, profile, doc) ? 'safe_auto' : 'none'
  else if (entry.fix === 'user_choice') {
    base.fixKind = fixOptions(base, profile).length ? 'user_choice' : 'none'
  }
  return base
}

/** 同一指纹只留第一条（同规则同对象同字段 = 同一件事）。 */
function dedupe(issues: ValidationIssue[]): ValidationIssue[] {
  const seen = new Set<string>()
  const out: ValidationIssue[] = []
  for (const i of issues) {
    if (seen.has(i.issueId)) continue
    seen.add(i.issueId)
    out.push(i)
  }
  return out
}

/** 整个项目跑一遍（每张画布一片）。 */
export function validateProject(input: ValidateInput): CanvasResult[] {
  return input.canvases.map((c) =>
    validateCanvas(c, input.documentId, input.assets, input.render),
  )
}

/* ---------------------------- 导出上下文规则 ------------------------------- */

export interface ExportContext {
  formats: string[]
  dpi: number
}

/**
 * 「现在就按这些格式与这个 PPI 出一张图」带来的问题。
 *
 * **与 `codex-plugin/mcp/tavotto_mcp/bridge.py` 的 `export_raster_issues()`
 * 严格同源**：同一个 rule code（`raster-dpi`）、同一个 message key
 * （`errors:preflight.exportRasterDpi`）、同一份 severity 表。另起一个 code
 * 的话，期刊覆盖里把 `raster-dpi` 调成 warn 对导出这条路就不生效了——同一份
 * 规范在两条入口上说不同的话。
 *
 * 主语是**这次导出请求**，不是某个对象：`objectId` / `gid` 都是 null，
 * 于是它与面板那条 `raster-dpi`（主语是某张位图素材）指纹不同、不会互相
 * 顶掉，也不会重复报同一件事。
 */
export function exportContextRaw(
  ctx: ExportContext,
  profile: PublicationProfile,
): PreflightIssue[] {
  const raster = new Set(
    (profile.preferred_formats.raster ?? []).map((f) => String(f).toLowerCase()),
  )
  const hit = ctx.formats.filter((f) => raster.has(String(f).toLowerCase()))
  const min = Number(profile.min_raster_dpi)
  const got = Number(ctx.dpi)
  if (!hit.length || !Number.isFinite(min) || !min || !Number.isFinite(got) || got >= min) return []
  const message = msg(
    'preflight.exportRasterDpi',
    { formats: hit.join('/'), dpi: String(got), min: String(min) },
    'errors',
  )
  const detail = { dpi: got, min_dpi: min, formats: hit }
  return [
    {
      id: 'raster-dpi',
      severity: severityOf(profile, 'raster-dpi'),
      message,
      objectIds: [],
      gids: [],
      detail,
      occurrences: [{ objectId: null, gid: null, prop: 'export.dpi', message, detail }],
    },
  ]
}

/** 同一条规则的可定位投影（问题面板与导出摘要用）。 */
export function exportContextIssues(
  ctx: ExportContext,
  profile: PublicationProfile,
  ref: { documentId: string; canvasId: string },
): ValidationIssue[] {
  return exportContextRaw(ctx, profile).flatMap((item) =>
    item.occurrences.map((occ) => {
      const objectRef: ObjectRef = { ...ref, objectId: null, gid: null }
      return {
        issueId: fingerprintOf(item.id, objectRef, occ.prop),
        ruleCode: item.id,
        severity: item.severity,
        context: 'export' as const,
        objectRef,
        subject: { kind: 'page' as const },
        propertyPath: occ.prop,
        message: occ.message,
        technicalDetails: occ.detail,
        fixKind: 'none' as const,
      }
    }),
  )
}

/* -------------------------------- 汇总 ------------------------------------ */

export type ValidationScope = 'project' | 'activeCanvas'

export interface ValidationSummary {
  counts: Record<Severity, number>
  total: number
  /** error 非空 = 默认阻止导出（用户可显式确认后强制导出） */
  blocking: boolean
  issues: ValidationIssue[]
  /**
   * 算完过至少一次。**`total === 0` 单独看不足以说"通过"**——还没查过与查过
   * 没问题是两个答案，压成一个的话导出对话框会在检查跑起来之前先说一句
   * 「检查通过」，那是这套服务能犯的最坏的错。
   */
  ready: boolean
  /** 上一次检查没查成（清单可能是更早那一次留下的） */
  failed: boolean
}

const EMPTY_COUNTS = (): Record<Severity, number> => ({
  error: 0,
  warn: 0,
  not_verifiable: 0,
  suggestion: 0,
})

/**
 * `state` 必须由调用方明说。**不给默认值**：默认成 `ready: true` 的话，
 * "还没查"会静默变成"查过了、没问题"。
 */
export function summarizeIssues(
  issues: ValidationIssue[],
  state: { ready: boolean; failed: boolean },
): ValidationSummary {
  const counts = EMPTY_COUNTS()
  for (const i of issues) counts[i.severity] += 1
  return {
    counts,
    total: issues.length,
    blocking: counts.error > 0,
    issues,
    ready: state.ready,
    failed: state.failed,
  }
}

/**
 * 文档问题 + 导出上下文问题。**按指纹去重**：两边都报同一件事时文档那条
 * 说了算（它在没打开导出对话框时也看得见）。
 */
export function mergeExportIssues(
  documentIssues: ValidationIssue[],
  exportIssues: ValidationIssue[],
): ValidationIssue[] {
  const seen = new Set<string>()
  return [...documentIssues, ...exportIssues].filter((i) => {
    if (seen.has(i.issueId)) return false
    seen.add(i.issueId)
    return true
  })
}

/**
 * 摘要的**唯一组装实现**。store 的 `getValidationSummary()` 与 React 组件
 * （订阅了 issues 之后现算）都走它——两份实现迟早分叉，而分叉的表现是
 * 「面板说 3 条、导出对话框说 2 条」。
 */
export function summaryFor(
  issues: ValidationIssue[],
  opts: {
    /** 只看这张画布；不给就是整个项目 */
    canvasId?: string
    extra?: ValidationIssue[]
    ready: boolean
    failed: boolean
  },
): ValidationSummary {
  const base = opts.canvasId
    ? issues.filter((i) => i.objectRef.canvasId === opts.canvasId)
    : issues
  return summarizeIssues(mergeExportIssues(base, opts.extra ?? []), {
    ready: opts.ready,
    failed: opts.failed,
  })
}

/* ------------------------------- 筛选 ------------------------------------- */

export interface IssueFilter {
  severities?: Severity[]
  canvasId?: string
  ruleCode?: string
}

export function filterIssues(issues: ValidationIssue[], filter?: IssueFilter): ValidationIssue[] {
  if (!filter) return issues
  return issues.filter(
    (i) =>
      (!filter.severities?.length || filter.severities.includes(i.severity)) &&
      (!filter.canvasId || i.objectRef.canvasId === filter.canvasId) &&
      (!filter.ruleCode || i.ruleCode === filter.ruleCode),
  )
}

/** 显示用成文（组件里配合 useTranslation 订阅语言变化）。 */
export const issueMessageText = (issue: ValidationIssue): string => formatMessage(issue.message)

/** 画布数据 → 检查输入的一片（激活画布由调用方用 `doc` 覆盖）。 */
export function canvasInput(
  canvas: CanvasData,
  doc: FigureDocument,
  profile: PublicationProfile,
): CanvasInput {
  return { canvasId: canvas.id, canvasName: canvas.name, doc, profile }
}
