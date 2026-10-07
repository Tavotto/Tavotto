import { useContext, useState, type ComponentType, type CSSProperties, type ReactNode } from 'react'
import {
  Blend,
  ChartLine,
  CircleQuestionMark,
  FileExclamationPoint,
  Fullscreen,
  Image,
  LayoutGrid,
  Type,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { listRowClass, rowMetaClass } from '@/components/ui/listRow'
import { TreeChevron, treeIndent } from '@/components/ui/TreeRow'
import { engineLabel } from '@/components/inspector/roles/registry'
import { t as translate } from '@/i18n'
import { figureOfPanel, figureThumbSrc } from '@/lib/exportFigures'
import { useRetryingSrc } from '@/lib/imgRetry'
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
import { SEVERITY_ICON, SEVERITY_INK } from '@/lib/validationText'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { useBatchable } from './IssueFixButton'
import { ProblemTreeContext, TRAIL_PAD, useHot, type ProblemTreeCtx } from './problemTree'
import { FixCountButton, TrailCell } from './ProblemTreeRows'

/** 本组文案在 errors:problems.* 下 */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/*
 * 问题树的**分桶层**（2026-09-28 卡片层 → 2026-10-07 设计审计 §9.4 披露树）。
 *
 * 先分类、再批量：一个节点是一张图 / 一个子图（按图）或一类问题（按类别），只说
 * 「多少项、有没有阻断」+ 这一支的「修复 N」；**就地展开**才是按规则聚合的逐组清单
 * （不再整页钻进去：此前点卡片换一屏、返回再换回来）。节点只是**同一份清单的另一种切法**：
 * 分桶判据全在 `lib/problemList.ts`，修复集合全走 `batchable()`（组件里 `useBatchable`），这里不做任何判断。
 *
 * 机器标识照旧（新手教程与 e2e 认它们）：`li[data-problem-card=<kind>]` 的**第一个子元素是展开钮**
 * （`> button:first-child`），`data-problem-card-key` = `drillKey()`，`-rules` / `-objects` / `-count` 是内容。
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

const CATEGORY_ICON: Record<ProblemCategory, ComponentType<{ size?: number; className?: string }>> = {
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

const useTree = (): ProblemTreeCtx => {
  const ctx = useContext(ProblemTreeContext)
  if (!ctx) throw new Error('ProblemTreeContext missing')
  return ctx
}

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
 * 取不到图就退回图标，不摆一个裂图。尺寸两档：图行 24、子图行 20，圆角 4（缩略图半径族 4 / 6）。
 */
export function ProblemThumb({
  objectId,
  part,
  size = 'md',
  fallback: Fallback = Image,
}: {
  objectId: string | null
  part?: SubplotPart | null
  size?: 'md' | 'sm'
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
  const frame = cn(
    'relative flex shrink-0 items-center justify-center overflow-hidden rounded-xs',
    size === 'md' ? 'size-6' : 'size-5',
  )
  if (!src || failed === src) {
    return (
      <span aria-hidden className={cn(frame, 'bg-surface-hover text-ink-2')}>
        <Fallback size={ICON_SIZE.sm} />
      </span>
    )
  }
  const crop = part && figure?.sizeMm ? cropStyle(part.bbox, figure.sizeMm) : null
  return (
    // 缩略图里是图本身（纸），底色用纸白；外框一圈 border 把它从行底上切开
    <span aria-hidden data-problem-thumb className={cn(frame, 'bg-white inset-ring inset-ring-border')}>
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

/* ---------------------------------- 节点 ---------------------------------- */

/** 各等级几项，给可达名与 title 用（「阻断 6 · 警告 2」）；视觉上尾随格只说阻断数与总数 */
function severityDetail(issues: readonly ValidationIssue[]): string {
  const n = (s: Severity) => issues.filter((i) => i.severity === s).length
  return [
    n('error') > 0 && pr('cardBlocking', { count: n('error') }),
    n('warn') > 0 && pr('cardWarn', { count: n('warn') }),
    n('suggestion') > 0 && pr('cardSuggestion', { count: n('suggestion') }),
  ]
    .filter(Boolean)
    .join(' · ')
}

/**
 * 一个分桶节点（图 / 子图 / 类别 / 无法核验）。**行是一颗展开钮，「修复 N」是它的兄弟节点**——按钮套
 * 按钮在辅助技术里读不出来。尾随格静止时是「⬣ 阻断数 · 总数」，指到 / 聚焦时同一格换成「修复 N」。
 */
function BucketNode({
  drill,
  title,
  glyph,
  issues,
  depth,
  big = false,
  muted = false,
  dflt = false,
  objectId = null,
  withFix = true,
  children,
}: {
  drill: ProblemDrill
  title: string
  glyph: ReactNode
  issues: ValidationIssue[]
  depth: number
  /** 图那一层是 32，其余 28 */
  big?: boolean
  /** 「无法核验」那一行：次一档的墨色 */
  muted?: boolean
  dflt?: boolean
  /** 指着这一行时画布上描哪个对象（图 / 子图行） */
  objectId?: string | null
  withFix?: boolean
  /** 展开后的内容；不给就是这一支的逐组清单 */
  children?: ReactNode
}) {
  const tree = useTree()
  const open = tree.isOpen(drill, dflt)
  // 点名一支 = 点名这一堆：建议档一起修（与组的「修复 N」同一个口径）
  const fixable = useBatchable(issues, tree.activeCanvasId, { includeSuggestions: true })
  const { hot, bind } = useHot()
  const rules = [...new Set(issues.map((i) => i.ruleCode))]
  const objects = [...new Set(issues.map((i) => i.objectRef.objectId).filter(Boolean))]
  const blocking = issues.filter((i) => i.severity === 'error').length
  const point = (on: boolean) => {
    if (objectId) useUiStore.getState().setIssueHover(on ? { objectId, gid: null } : null)
  }
  const rowBind = {
    ...bind,
    onPointerEnter: () => {
      bind.onPointerEnter()
      point(true)
    },
    onPointerLeave: () => {
      bind.onPointerLeave()
      point(false)
    },
  }
  const detail = severityDetail(issues)
  return (
    <li
      data-problem-card={drill.kind}
      data-problem-card-key={drillKey(drill)}
      data-problem-card-count={issues.length}
      data-problem-card-rules={rules.join(' ')}
      data-problem-card-objects={objects.join(' ')}
      className="relative"
    >
      <button
        type="button"
        {...rowBind}
        onClick={() => tree.toggle(drill, dflt)}
        aria-expanded={open}
        aria-label={pr('nodeLabel', { name: title, count: issues.length, detail })}
        title={detail || undefined}
        style={treeIndent(depth)}
        className={cn(
          listRowClass({ size: 'sm', muted }),
          big && 'h-8 text-sm',
          TRAIL_PAD,
          'w-[calc(100%-0.5rem)] gap-1.5 text-left',
        )}
      >
        <TreeChevron expanded={open} />
        {glyph}
        <span className={cn('min-w-0 flex-1 truncate', big && 'font-medium')}>{title}</span>
      </button>
      <TrailCell
        hot={hot}
        bind={rowBind}
        height={big ? 'h-8' : 'h-7'}
        rest={
          <span className={cn(rowMetaClass(), 'flex items-center gap-1')}>
            {blocking > 0 && (
              <span className="flex items-center gap-0.5 text-danger-content">
                <SeverityGlyph severity="error" />
                {blocking}
                <span aria-hidden className="text-ink-faint">
                  ·
                </span>
              </span>
            )}
            {issues.length}
          </span>
        }
        action={withFix && fixable.length > 0 ? <FixCountButton issues={fixable} /> : undefined}
      />
      {open && (
        <ul data-problem-tier={drill.kind === 'unverifiable' ? 'unverifiable' : 'actionable'}>
          {children ?? tree.body(drill, depth + 1)}
        </ul>
      )}
    </li>
  )
}

/** 等级记号：一张表（`SEVERITY_ICON` / `SEVERITY_INK`），全面板同一副 */
export function SeverityGlyph({ severity, size = 'xs' }: { severity: Severity; size?: 'xs' | 'sm' }) {
  const Icon = SEVERITY_ICON[severity]
  return <Icon size={ICON_SIZE[size]} aria-hidden className={cn('shrink-0', SEVERITY_INK[severity])} />
}

/* -------------------------------- 两种切法 -------------------------------- */

/**
 * 按图。一张图拆得出子图时，每个子图一支；「整份排版」下（或清单里不止一张图时）子图挂在
 * 一行图头下面（图头默认开着——它只是个分组头）。清单里不止一张图时图头才有「修复 N」——只有
 * 一张图时那颗就是顶上的「全部修复」，不再摆第二颗。拆不出子图的图就是一支。
 */
export function FigureCards({ figures, withHeaders }: { figures: FigureBucket[]; withHeaders: boolean }) {
  return (
    <>
      {figures.map((f) => {
        const name = figureName(f)
        if (!isSplit(f)) {
          return (
            <BucketNode
              key={f.key}
              drill={{ kind: 'figure', key: f.key }}
              title={name}
              issues={f.issues}
              depth={0}
              big
              objectId={f.objectId}
              glyph={<ProblemThumb objectId={f.objectId} fallback={f.objectId ? Image : LayoutGrid} />}
            />
          )
        }
        const parts = f.parts.map((p) => <PartNode key={p.key} figure={f} part={p} depth={withHeaders ? 1 : 0} />)
        if (!withHeaders) return parts
        return (
          <BucketNode
            key={f.key}
            drill={{ kind: 'figure', key: f.key }}
            title={name}
            issues={f.issues}
            depth={0}
            big
            dflt
            objectId={f.objectId}
            withFix={figures.length > 1}
            glyph={<ProblemThumb objectId={f.objectId} />}
          >
            {parts}
          </BucketNode>
        )
      })}
    </>
  )
}

function PartNode({ figure, part, depth }: { figure: FigureBucket; part: PartBucket; depth: number }) {
  return (
    <BucketNode
      drill={{ kind: 'part', figure: figure.key, key: part.key }}
      title={partName(part.part)}
      issues={part.issues}
      depth={depth}
      objectId={figure.objectId}
      glyph={<ProblemThumb objectId={figure.objectId} part={part.part} size="sm" />}
    />
  )
}

/** 按类别：一类一支 */
export function CategoryCards({ categories }: { categories: CategoryBucket[] }) {
  return (
    <>
      {categories.map((c) => {
        const Icon = CATEGORY_ICON[c.key]
        return (
          <BucketNode
            key={c.key}
            drill={{ kind: 'category', key: c.key }}
            title={categoryLabel(c.key)}
            issues={c.issues}
            depth={0}
            glyph={<Icon size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-3" />}
          />
        )
      })}
    </>
  )
}

/**
 * 「无法自动检查」在树底只占一行（它们要的是人眼确认，不是修复）：虚线圆 + 项数，展开是它自己的清单。
 */
export function UnverifiableEntry({ issues }: { issues: ValidationIssue[] }) {
  return (
    <BucketNode
      drill={{ kind: 'unverifiable' }}
      title={pr('tierUnverifiable')}
      issues={issues}
      depth={0}
      muted
      withFix={false}
      glyph={<SeverityGlyph severity="not_verifiable" size="sm" />}
    />
  )
}
