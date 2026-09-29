import { useState, type ComponentType, type CSSProperties, type ReactNode } from 'react'
import {
  ArrowLeft,
  Blend,
  ChartLine,
  ChevronRight,
  CircleDashed,
  CircleQuestionMark,
  FileExclamationPoint,
  Fullscreen,
  Image,
  LayoutGrid,
  Type,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { engineLabel } from '@/components/inspector/roles/registry'
import { t as translate } from '@/i18n'
import { figureOfPanel, figureThumbSrc } from '@/lib/exportFigures'
import { useRetryingSrc } from '@/lib/imgRetry'
import { problemContextNow } from '@/lib/problemContext'
import {
  drillKey,
  isSplit,
  type CategoryBucket,
  type FigureBucket,
  type PartBucket,
  type ProblemDrill,
} from '@/lib/problemList'
import type { Severity } from '@/lib/profile'
import type { SubplotPart } from '@/lib/subplotParts'
import { cn } from '@/lib/utils'
import type { ProblemCategory, ValidationIssue } from '@/lib/validation'
import { issueTitle } from '@/lib/validationText'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { runBatchFix, useBatchable } from './IssueFixButton'

/** 本组文案在 errors:problems.* 下 */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/*
 * 问题面板的**卡片层**（2026-09-28 用户反馈：一屏 178 行逐条铺开太吵）。
 *
 * 先分类、再批量：一张卡片是一个子图（按图）或一类问题（按类别），只说
 * 「多少项、有没有阻断、最主要的是什么」+ 这张卡的「修复 N」；点卡片才进
 * `ProblemPanel` 原来那份逐组清单。卡片只是**同一份清单的另一种切法**：分桶
 * 判据全在 `lib/problemList.ts`，修复集合全走 `batchable()`（组件里 `useBatchable`），这里不做任何判断。
 */

/* --------------------------------- 名字 ----------------------------------- */

const CATEGORY_LABEL: Record<ProblemCategory, () => string> = {
  text: () => pr('category.text'),
  lines: () => pr('category.lines'),
  layout: () => pr('category.layout'),
  color: () => pr('category.color'),
  file: () => pr('category.file'),
  other: () => pr('category.other'),
}

const CATEGORY_ICON: Record<ProblemCategory, ComponentType<{ size?: number }>> = {
  text: Type,
  lines: ChartLine,
  layout: Fullscreen,
  color: Blend,
  file: FileExclamationPoint,
  other: CircleQuestionMark,
}

export const categoryLabel = (c: ProblemCategory) => CATEGORY_LABEL[c]()

/** 一张图叫什么：面板名 / 文件名主干（`subject.objectName`）；页面级问题是「整张画布」 */
export const figureName = (f: Pick<FigureBucket, 'objectId' | 'issues'>): string =>
  f.objectId ? (f.issues[0].subject.objectName ?? pr('subjectPanel')) : pr('subjectPage')

/** 子图叫什么：图里写着「(a)」就用它，否则引擎的「子图 N」；整图那份是「整张图」 */
export const partName = (part: SubplotPart | null): string =>
  part ? (part.tag ? pr('partTag', { tag: part.tag }) : engineLabel(part.label)) : pr('partWhole')

/* -------------------------------- 缩略图 ---------------------------------- */

function usePanelObject(objectId: string | null): PanelObject | null {
  return useDocumentStore((s) => {
    if (!objectId) return null
    const hit =
      s.doc.objects.find((o) => o.id === objectId) ??
      s.canvases.flatMap((c) => c.objects).find((o) => o.id === objectId)
    return hit?.type === 'panel' ? hit : null
  })
}

/**
 * 认出是哪张图 / 哪个子图就够：画的是素材的分档缩略图（与导出对话框同一个地址，
 * `figureThumbSrc`），子图按 `SubplotPart.bbox` 裁成方块——不为缩略图新跑渲染。
 * 取不到图就退回图标，不摆一个裂图。
 */
export function ProblemThumb({
  objectId,
  part,
  fallback: Fallback = Image,
}: {
  objectId: string | null
  part?: SubplotPart | null
  fallback?: ComponentType<{ size?: number }>
}) {
  const panel = usePanelObject(objectId)
  // 订阅换代：写回原图 / 重新运行之后缩略图跟着换
  useAssetStore((s) => (panel ? s.byId[panel.fileId]?.mtime : undefined))
  useRuntimeAssetStore((s) => (panel ? s.previewNonce[panel.fileId] : undefined))
  const [failed, setFailed] = useState<string | null>(null)
  const figure = panel ? figureOfPanel(panel) : null
  const src = figure ? figureThumbSrc(figure, 160) : null
  // PDF / 浏览器画不了的位图走 `/api/render`：一次 503 可能只是背压，先按共享的退避表
  // 重取（`lib/imgRetry`），真取不到了才退回图标
  const retry = useRetryingSrc(src ?? '', () => setFailed(src))
  const frame = 'relative flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-xs'
  if (!src || failed === src) {
    return (
      <span aria-hidden className={cn(frame, 'bg-surface-2 text-ink-2')}>
        <Fallback size={ICON_SIZE.md} />
      </span>
    )
  }
  const crop = part && figure?.sizeMm ? cropStyle(part.bbox, figure.sizeMm) : null
  return (
    <span aria-hidden data-problem-thumb className={cn(frame, 'border border-border bg-white')}>
      <img
        src={retry.src}
        alt=""
        draggable={false}
        loading="lazy"
        onError={retry.onError}
        className={crop ? 'absolute max-w-none' : 'h-full w-full object-contain'}
        style={crop ?? undefined}
      />
    </span>
  )
}

/**
 * 把图里的一块（figure 分数、y 向下）放进正方形格子：按毫米算，免得非方形的图
 * 被拉扁；四周留 4% 的边，让子图的标签与刻度也在格子里。
 */
function cropStyle(
  [x, y, w, h]: [number, number, number, number],
  [W, H]: [number, number],
): CSSProperties | null {
  if (!(W > 0 && H > 0 && w > 0 && h > 0)) return null
  const pad = 0.04
  const x0 = Math.max(0, x - pad) * W
  const y0 = Math.max(0, y - pad) * H
  const x1 = Math.min(1, x + w + pad) * W
  const y1 = Math.min(1, y + h + pad) * H
  const side = Math.max(x1 - x0, y1 - y0)
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  const pct = (v: number) => `${(v / side) * 100}%`
  return { width: pct(W), height: pct(H), left: pct(side / 2 - cx), top: pct(side / 2 - cy) }
}

/* --------------------------------- 卡片 ----------------------------------- */

/** 等级色只在缩略图 / 图标角上的一颗点里（颜色不是唯一表达：卡片副标题写着「N 项阻断」） */
const SEVERITY_DOT: Partial<Record<Severity, string>> = {
  error: 'bg-danger',
  warn: 'bg-warn',
}

function CategoryGlyph({ category, severity }: { category: ProblemCategory; severity: Severity }) {
  const Icon = CATEGORY_ICON[category]
  return (
    <span aria-hidden className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-ink-2">
      <Icon size={ICON_SIZE.md} />
      <SeverityDot severity={severity} />
    </span>
  )
}

function SeverityDot({ severity }: { severity: Severity }) {
  const color = SEVERITY_DOT[severity]
  if (!color) return null
  return <span className={cn('absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ring-2 ring-surface', color)} />
}

/**
 * 图 / 子图卡片的第二行：各等级几项（「6 项阻断 · 2 项警告」）。一张子图卡片上最要紧的
 * 是「有没有会拦住导出的」，检查项名字在点进去之后（2026-09-28 实测：「阻断 + 规则名」
 * 在 300px 的抽屉里被截成「字号低于…」，两样都没说清）。
 */
function severityLine(issues: readonly ValidationIssue[]): ReactNode {
  const n = (s: Severity) => issues.filter((i) => i.severity === s).length
  const parts = [
    n('error') > 0 && <span key="e" className="text-danger">{pr('cardBlocking', { count: n('error') })}</span>,
    n('warn') > 0 && <span key="w">{pr('cardWarn', { count: n('warn') })}</span>,
    n('suggestion') > 0 && <span key="s">{pr('cardSuggestion', { count: n('suggestion') })}</span>,
  ].filter(Boolean)
  return parts.flatMap((p, i) => (i ? [<span key={`d${i}`} aria-hidden> · </span>, p] : [p]))
}

/** 类别卡片的第二行：阻断数（有才说）· 最主要的那条检查项（「等 N 种」）· 可选的补充 */
function metaLine(issues: readonly ValidationIssue[], extra?: string): ReactNode {
  const blocking = issues.filter((i) => i.severity === 'error').length
  const counts = new Map<string, { n: number; first: ValidationIssue }>()
  for (const i of issues) {
    const c = counts.get(i.ruleCode)
    if (c) c.n += 1
    else counts.set(i.ruleCode, { n: 1, first: i })
  }
  // 最主要的 = 等级最高里项数最多的；同样多就按清单里先出现的
  const rank = (s: Severity) => ['error', 'warn', 'not_verifiable', 'suggestion'].indexOf(s)
  const top = [...counts.values()].sort(
    (a, b) => rank(a.first.severity) - rank(b.first.severity) || b.n - a.n,
  )[0]
  const title = top ? issueTitle(top.first) : ''
  const kinds = counts.size > 1 ? pr('cardKinds', { title, count: counts.size }) : title
  return (
    <>
      {blocking > 0 && (
        <>
          <span className="text-danger">{pr('cardBlocking', { count: blocking })}</span>
          <span aria-hidden> · </span>
        </>
      )}
      <span>{kinds}</span>
      {extra && (
        <>
          <span aria-hidden> · </span>
          <span>{extra}</span>
        </>
      )}
    </>
  )
}

/**
 * 一张卡片。**整张卡是一颗「查看详情」按钮，「修复 N」是它的兄弟节点**——按钮套
 * 按钮在辅助技术里读不出来（与清单行同一个理由）。修复钮在右、箭头在最右。
 *
 * `data-problem-card-key`：这是哪张卡片（`drillKey`，新手教程按 `drillKeysOf(issue)`
 * 找「那条问题所在的卡片」）；`-rules` / `-objects`：卡里有哪些规则、哪些对象（空格
 * 分隔，配 `~=`，给 e2e 按内容找卡片）。都是机器标识，不是文案。
 */
function ProblemCard({
  drill,
  title,
  glyph,
  issues,
  meta,
  activeCanvasId,
}: {
  drill: ProblemDrill
  title: string
  glyph: ReactNode
  issues: ValidationIssue[]
  meta: ReactNode
  activeCanvasId: string
}) {
  const fixing = useUiStore((s) => s.fixing)
  // 点名一张卡片 = 点名这一堆：建议档一起修（与组头「全部修复」同一个口径）
  const fixable = useBatchable(issues, activeCanvasId, { includeSuggestions: true })
  const manual = issues.every((i) => i.fixKind === 'none')
  const rules = [...new Set(issues.map((i) => i.ruleCode))]
  const objects = [...new Set(issues.map((i) => i.objectRef.objectId).filter(Boolean))]
  return (
    <li
      data-problem-card={drill.kind}
      data-problem-card-key={drillKey(drill)}
      data-problem-card-count={issues.length}
      data-problem-card-rules={rules.join(' ')}
      data-problem-card-objects={objects.join(' ')}
      className="relative rounded-md bg-surface shadow-card"
    >
      <button
        type="button"
        onClick={() => useUiStore.getState().setProblemDrill(drill, problemContextNow())}
        aria-label={pr('cardOpen', { name: title, count: issues.length })}
        className={cn(
          'flex w-full items-center gap-2.5 rounded-md py-2 pl-2 text-left outline-none',
          'transition-colors duration-fast hover:bg-surface-hover focus-visible:focus-ring',
          fixable.length > 0 || manual ? 'pr-24' : 'pr-7',
        )}
      >
        {glyph}
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-1.5">
            <span className="min-w-0 truncate text-sm font-medium leading-5 text-ink">{title}</span>
            <span className="type-meta shrink-0 tabular-nums">{issues.length}</span>
          </span>
          <span className="type-meta block truncate leading-4">{meta}</span>
        </span>
      </button>
      <span className="pointer-events-none absolute inset-y-0 right-1 flex items-center gap-0.5">
        {fixable.length > 0 ? (
          <Button
            size="sm"
            variant="ghost"
            className="pointer-events-auto tabular-nums text-ink-2 hover:text-ink"
            disabled={fixing}
            title={pr('cardFixTip', { count: fixable.length })}
            onClick={() => void runBatchFix(fixable, { includeSuggestions: true })}
          >
            {pr('cardFix', { count: fixable.length })}
          </Button>
        ) : manual ? (
          <span className="pointer-events-auto px-1.5 text-xs text-ink-3" title={pr('cardManualTip')}>
            {pr('cardManual')}
          </span>
        ) : null}
        <ChevronRight size={ICON_SIZE.xs} aria-hidden className="text-ink-3" />
      </span>
    </li>
  )
}

/* -------------------------------- 两种切法 -------------------------------- */

/**
 * 按图。一张图拆得出子图时，每个子图一张卡；「整份排版」下（或清单里不止一张图时）
 * 子图卡片挂在一行图头下面。清单里不止一张图时图头才有「修复本图」——只有一张图时
 * 那颗就是顶上的「全部处理」，不再摆第二颗。拆不出子图的图就是一张整图卡。
 */
export function FigureCards({
  figures,
  withHeaders,
  activeCanvasId,
}: {
  figures: FigureBucket[]
  withHeaders: boolean
  activeCanvasId: string
}) {
  return (
    <>
      {figures.map((f) => {
        const name = figureName(f)
        if (!isSplit(f)) {
          return (
            <ProblemCard
              key={f.key}
              drill={{ kind: 'figure', key: f.key }}
              title={name}
              issues={f.issues}
              meta={severityLine(f.issues)}
              activeCanvasId={activeCanvasId}
              glyph={
                <Glyph severity={f.severity}>
                  <ProblemThumb objectId={f.objectId} fallback={f.objectId ? Image : LayoutGrid} />
                </Glyph>
              }
            />
          )
        }
        const parts = f.parts.map((p) => (
          <PartCard key={p.key} figure={f} part={p} activeCanvasId={activeCanvasId} />
        ))
        if (!withHeaders) return parts
        return (
          <li key={f.key} data-problem-figure className="flex flex-col gap-1.5">
            <FigureHeader figure={f} name={name} activeCanvasId={activeCanvasId} withFix={figures.length > 1} />
            <ul className="ml-4 flex flex-col gap-1.5 border-l border-border pl-2.5">{parts}</ul>
          </li>
        )
      })}
    </>
  )
}

function PartCard({
  figure,
  part,
  activeCanvasId,
}: {
  figure: FigureBucket
  part: PartBucket
  activeCanvasId: string
}) {
  return (
    <ProblemCard
      drill={{ kind: 'part', figure: figure.key, key: part.key }}
      title={partName(part.part)}
      issues={part.issues}
      meta={severityLine(part.issues)}
      activeCanvasId={activeCanvasId}
      glyph={
        <Glyph severity={part.severity}>
          <ProblemThumb objectId={figure.objectId} part={part.part} />
        </Glyph>
      }
    />
  )
}

function Glyph({ severity, children }: { severity: Severity; children: ReactNode }) {
  return (
    <span className="relative shrink-0">
      {children}
      <SeverityDot severity={severity} />
    </span>
  )
}

function FigureHeader({
  figure,
  name,
  activeCanvasId,
  withFix,
}: {
  figure: FigureBucket
  name: string
  activeCanvasId: string
  /** 清单里只有这一张图时，「修复本图」就是顶上的「全部处理」，不摆第二颗 */
  withFix: boolean
}) {
  const fixing = useUiStore((s) => s.fixing)
  const fixable = useBatchable(figure.issues, activeCanvasId, { includeSuggestions: true })
  const parts = figure.parts.filter((p) => p.part).length
  return (
    <div className="flex items-center gap-1 pr-1">
      <button
        type="button"
        onClick={() => useUiStore.getState().setProblemDrill({ kind: 'figure', key: figure.key }, problemContextNow())}
        aria-label={pr('cardOpen', { name, count: figure.issues.length })}
        className={cn(
          'flex min-w-0 flex-1 items-center gap-2.5 rounded-sm py-1 pl-1 pr-1.5 text-left outline-none',
          'transition-colors duration-fast hover:bg-surface-hover focus-visible:focus-ring',
        )}
      >
        <ProblemThumb objectId={figure.objectId} />
        <span className="min-w-0 flex-1">
          <span className="type-section block truncate leading-5">{name}</span>
          <span className="type-meta block leading-4 tabular-nums">
            {pr('figureMeta', { count: figure.issues.length, parts })}
          </span>
        </span>
      </button>
      {withFix && fixable.length > 0 && (
        <Button
          size="sm"
          variant="ghost"
          className="shrink-0 tabular-nums text-ink-2 hover:text-ink"
          disabled={fixing}
          title={pr('cardFixTip', { count: fixable.length })}
          onClick={() => void runBatchFix(fixable, { includeSuggestions: true })}
        >
          {`${pr('fixFigure')} ${fixable.length}`}
        </Button>
      )}
    </div>
  )
}

/** 按类别：一类一张，副标题补一句「涉及几个子图 / 几张图」 */
export function CategoryCards({
  categories,
  activeCanvasId,
}: {
  categories: CategoryBucket[]
  activeCanvasId: string
}) {
  return (
    <>
      {categories.map((c) => {
        const parts = new Set(c.issues.map((i) => `${i.objectRef.objectId}|${i.subject.part?.key ?? ''}`)).size
        return (
          <ProblemCard
            key={c.key}
            drill={{ kind: 'category', key: c.key }}
            title={categoryLabel(c.key)}
            issues={c.issues}
            meta={metaLine(c.issues, parts > 1 ? pr('cardParts', { count: parts }) : undefined)}
            activeCanvasId={activeCanvasId}
            glyph={<CategoryGlyph category={c.key} severity={c.severity} />}
          />
        )
      })}
    </>
  )
}

/** 「无法自动检查」那一段在卡片层只占一行：它们要的是人眼确认，不是修复 */
export function UnverifiableEntry({ count }: { count: number }) {
  return (
    <li>
      <button
        type="button"
        data-problem-card="unverifiable"
        data-problem-card-key={drillKey({ kind: 'unverifiable' })}
        onClick={() => useUiStore.getState().setProblemDrill({ kind: 'unverifiable' }, problemContextNow())}
        className={cn(
          'flex h-8 w-full items-center gap-2 rounded-sm px-2 text-left text-xs text-ink-2 outline-none',
          'transition-colors duration-fast hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
        )}
      >
        <CircleDashed size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-3" />
        <span className="min-w-0 flex-1 truncate tabular-nums">{pr('unverifiableEntry', { count })}</span>
        <ChevronRight size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
      </button>
    </li>
  )
}

/* ---------------------------------- 详情 ---------------------------------- */

/**
 * 点进一张卡片之后的头：返回、这张卡是谁、它的「修复」。这里的修复是这一屏
 * 唯一的填色主动作（总览里那颗「全部处理」此时不在）。
 */
export function DrillHeader({
  drill,
  figures,
  issues,
  activeCanvasId,
}: {
  drill: ProblemDrill
  figures: FigureBucket[]
  issues: ValidationIssue[]
  activeCanvasId: string
}) {
  const fixing = useUiStore((s) => s.fixing)
  const view = useUiStore((s) => s.problemView)
  const batch = useBatchable(issues, activeCanvasId, { includeSuggestions: true })
  const fixable = drill.kind === 'unverifiable' ? [] : batch
  const figure = drill.kind === 'part' || drill.kind === 'figure'
    ? figures.find((f) => f.key === (drill.kind === 'part' ? drill.figure : drill.key)) ?? null
    : null
  const part = drill.kind === 'part' ? (figure?.parts.find((p) => p.key === drill.key) ?? null) : null
  const objectId = figure?.objectId ?? (drill.kind === 'part' || drill.kind === 'figure' ? objectOf(drill) : null)
  let title: string
  let glyph: ReactNode
  let fixLabel: string
  if (drill.kind === 'category') {
    title = categoryLabel(drill.key)
    glyph = <CategoryGlyph category={drill.key} severity="suggestion" />
    fixLabel = pr('fixCategory')
  } else if (drill.kind === 'unverifiable') {
    title = pr('tierUnverifiable')
    glyph = (
      <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-ink-2">
        <CircleDashed size={ICON_SIZE.md} />
      </span>
    )
    fixLabel = ''
  } else if (drill.kind === 'part') {
    title = partName(part?.part ?? issues[0]?.subject.part ?? null)
    glyph = <ProblemThumb objectId={objectId} part={part?.part ?? issues[0]?.subject.part ?? null} />
    fixLabel = pr('fixPart')
  } else {
    title = figure ? figureName(figure) : issues[0] ? figureName({ objectId, issues }) : pr('subjectPanel')
    glyph = <ProblemThumb objectId={objectId} fallback={objectId ? Image : LayoutGrid} />
    fixLabel = pr('fixFigure')
  }
  const kinds = new Set(issues.map((i) => i.ruleCode)).size
  return (
    <div className="shrink-0 px-2 pb-2 pt-1.5">
      <button
        type="button"
        data-problem-back
        onClick={() => {
          const ui = useUiStore.getState()
          ui.setProblemDrill(null)
          ui.setProblemCursor(null)
        }}
        className={cn(
          'flex h-7 items-center gap-1 rounded-sm px-1.5 text-xs text-ink-2 outline-none',
          'transition-colors duration-fast hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
        )}
      >
        <ArrowLeft size={ICON_SIZE.sm} aria-hidden />
        {view === 'category' ? pr('backCategories') : pr('backFigures')}
      </button>
      <div className="mt-1.5 flex items-center gap-2.5 px-1">
        {glyph}
        <div className="min-w-0 flex-1">
          <h3 className="type-title truncate">{title}</h3>
          {issues.length > 0 && (
            <p className="type-meta leading-4 tabular-nums">
              {pr('drillMeta', { count: issues.length, kinds })}
            </p>
          )}
        </div>
        {fixable.length > 0 && (
          <Button
            size="md"
            variant="primary"
            className="shrink-0"
            disabled={fixing}
            title={pr('cardFixTip', { count: fixable.length })}
            onClick={() => void runBatchFix(fixable, { includeSuggestions: true })}
          >
            {fixing ? pr('fixing') : fixLabel}
          </Button>
        )}
      </div>
    </div>
  )
}

/** 卡片被修空之后图桶也没了：从键里取回面板 id，头上的缩略图不至于变成图标 */
const objectOf = (drill: Extract<ProblemDrill, { kind: 'part' | 'figure' }>): string | null => {
  const key = drill.kind === 'part' ? drill.figure : drill.key
  return key.startsWith('page:') ? null : key
}

