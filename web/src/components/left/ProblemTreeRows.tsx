import { useState, type FocusEvent, type ReactNode } from 'react'
import { MIN_HIDDEN_ROWS, PREVIEW_ROWS, TRAIL_PAD, TRAIL_W, useHot } from './problemTree'
import { ChevronDown, Info } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { listRowClass, rowMetaClass } from '@/components/ui/listRow'
import { TreeChevron, treeIndent } from '@/components/ui/TreeRow'
import { TruncateMiddle } from '@/components/ui/TruncateMiddle'
import { t as translate } from '@/i18n'
import type { IssueGroup } from '@/lib/problemList'
import { cn } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import {
  issueAriaLabel,
  issueDetailText,
  issueTitle,
  issueValues,
  severityLabel,
  SEVERITY_ICON,
  SEVERITY_INK,
  subjectName,
  technicalDetailLines,
} from '@/lib/validationText'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { Popover } from '../ui/Popover'
import { FixButton, runBatchFix, useBatchable } from './IssueFixButton'

/** 本组文案在 errors:problems.* 下 */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/*
 * 问题树的**行**（2026-10-07 设计审计 §9.4：卡片 → 披露树）。四种行一种语法：
 *
 *   [缩进][chevron][记号] 名字 ………………… [尾随格 88px]
 *
 * 行都建在 `listRowClass` 上（圆角 8、选中 = selected 底 + 600）：图 32 · 子图 / 类别 28 · 规则 28 · 对象 28。
 * **尾随格**宽高永不变：静止时是这一行的值（对象行「6.5 → 8 pt」，其余是项数），指到 / 聚焦 / 是「当前」时
 * **同一格**换成「修复」（对象行再加一颗 ⓘ 技术详情）——不挤名字、不改行高、不跳列。
 * 修复钮一直在 DOM 里、一直在 Tab 顺序里，静止时只是不画（opacity），键盘一样找得到。
 */

/**
 * 尾随格：`rest` 是静止时的值，`action` 是热的时候同一格里的动作。两层叠在一起，只换透明度——
 * 动作层在上（DOM 后写），指针落上去之前它已经因为 hover 变成可见的了。
 */
export function TrailCell({
  hot,
  bind,
  rest,
  action,
  height = 'h-7',
}: {
  hot: boolean
  bind: ReturnType<typeof useHot>['bind']
  rest: ReactNode
  action?: ReactNode
  height?: 'h-7' | 'h-8'
}) {
  return (
    <span
      {...bind}
      data-problem-trail
      className={cn('absolute right-2 top-0 flex items-center justify-end', TRAIL_W, height)}
    >
      <span
        data-problem-trail-rest
        className={cn(
          'flex min-w-0 items-center justify-end gap-1 transition-opacity duration-fast',
          hot && action ? 'opacity-0' : 'opacity-100',
        )}
      >
        {rest}
      </span>
      {action && (
        <span
          data-problem-trail-action
          className={cn(
            'absolute inset-0 flex items-center justify-end gap-0.5 transition-opacity duration-fast',
            hot ? 'opacity-100' : 'opacity-0',
          )}
        >
          {action}
        </span>
      )}
    </span>
  )
}

/** 「修复 N」：一个动词，三种形态（修复 / 修复… / 修复 N）里的批量那一种 */
export function FixCountButton({ issues }: { issues: ValidationIssue[] }) {
  const fixing = useUiStore((s) => s.fixing)
  return (
    <Button
      size="sm"
      variant="ghost"
      data-problem-fix-count={issues.length}
      className="tabular-nums"
      disabled={fixing}
      title={pr('cardFixTip', { count: issues.length })}
      onClick={() => void runBatchFix(issues, { includeSuggestions: true })}
    >
      {pr('fixCount', { count: issues.length })}
    </Button>
  )
}

/**
 * 一条规则一组。组头把「这是什么问题」说一遍并钉在滚动区顶上（底色读 `--drawer-bg`）；
 * 组内每行只说「谁、现在多少 → 要多少」。
 *
 * 行数达到 {@link PREVIEW_ROWS} + {@link MIN_HIDDEN_ROWS} 时只展开前几行，其余收进「显示其余 N 项」；
 * 「当前」那条落在被折起的部分时整组自动展开——逐项处理的「下一项」不能把用户带到一条看不见的行上。
 */
export function GroupNode({
  group,
  depth,
  open,
  expanded,
  onToggle,
  onExpand,
  currentId,
  activeCanvasId,
  onLocate,
}: {
  group: IssueGroup
  depth: number
  open: boolean
  expanded: boolean
  onToggle: () => void
  onExpand: () => void
  currentId: string | null
  activeCanvasId: string
  onLocate: (issue: ValidationIssue) => void
}) {
  const Icon = SEVERITY_ICON[group.severity]
  const title = issueTitle(group.issues[0])
  // 组的「修复 N」是用户点名这一组：建议档的组也照修（顶上的「全部修复」才不带建议档）
  const fixable = useBatchable(group.issues, activeCanvasId, { includeSuggestions: true })
  const { hot, bind } = useHot()
  const currentAt = currentId ? group.issues.findIndex((i) => i.issueId === currentId) : -1
  const folded =
    !expanded && currentAt < PREVIEW_ROWS && group.issues.length >= PREVIEW_ROWS + MIN_HIDDEN_ROWS
  const visible = folded ? group.issues.slice(0, PREVIEW_ROWS) : group.issues
  return (
    <li data-issue-group={group.ruleCode}>
      {/* 吸顶组头的底色必须与抽屉同色：停靠时抽屉坐在灰色桌面（bg）上，覆盖式才是白底（surface）。
          读 `LeftPanel` 按模式设的 `--drawer-bg`，抽屉外（测试 / 别处借用）退回 surface */}
      <div
        data-issue-group-head
        className="sticky top-0 z-sticky relative bg-[var(--drawer-bg,var(--color-surface))]"
      >
        <button
          type="button"
          {...bind}
          data-issue-group-toggle
          onClick={onToggle}
          aria-expanded={open}
          aria-label={pr('groupAria', {
            title,
            severity: severityLabel(group.severity),
            count: group.objects,
          })}
          style={treeIndent(depth)}
          className={cn(listRowClass({ size: 'sm' }), TRAIL_PAD, 'w-[calc(100%-0.5rem)] text-left')}
        >
          <TreeChevron expanded={open} />
          <Icon size={ICON_SIZE.sm} aria-hidden className={cn('shrink-0', SEVERITY_INK[group.severity])} />
          <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
        </button>
        <TrailCell
          hot={hot}
          bind={bind}
          rest={
            /* 受影响对象数 + 等级字（等级不只靠颜色与形状，problemPanel.test 钉着） */
            <span className={cn(rowMetaClass(), 'truncate')}>
              {pr('groupObjects', { count: group.objects })}
              <span className="sr-only">{` · ${severityLabel(group.severity)}`}</span>
            </span>
          }
          action={fixable.length > 0 ? <FixCountButton issues={fixable} /> : undefined}
        />
      </div>
      {open && (
        <ul>
          {visible.map((issue) => (
            <IssueRow
              key={issue.issueId}
              issue={issue}
              depth={depth + 1}
              current={issue.issueId === currentId}
              onLocate={() => onLocate(issue)}
              activeCanvasId={activeCanvasId}
            />
          ))}
          {folded && (
            <li>
              <button
                type="button"
                data-issue-show-rest
                onClick={onExpand}
                style={treeIndent(depth + 1)}
                className={cn(listRowClass({ size: 'sm', muted: true }), 'w-[calc(100%-0.5rem)] text-left')}
              >
                <span className="flex w-4 shrink-0 justify-center text-ink-3" aria-hidden>
                  <ChevronDown size={ICON_SIZE.xs} />
                </span>
                {pr('showRest', { count: group.issues.length - PREVIEW_ROWS })}
              </button>
            </li>
          )}
        </ul>
      )}
    </li>
  )
}

/**
 * 一个真实对象的一行：名字在左，尾随格静止时是「当前 → 要求」，热的时候换成「修复」+ ⓘ（技术详情）。
 * 整行点击 = 定位；修复与 ⓘ 是它的兄弟节点（按钮套按钮读不出来）。
 * 指着这一行时画布上那个对象描一道悬停轮廓（`uiStore.issueHover`）。
 */
export function IssueRow({
  issue,
  depth,
  current,
  onLocate,
  activeCanvasId,
}: {
  issue: ValidationIssue
  depth: number
  current: boolean
  onLocate: () => void
  activeCanvasId: string
}) {
  const values = issueValues(issue)
  const canvasName = useDocumentStore(
    (s) => s.canvases.find((c) => c.id === issue.objectRef.canvasId)?.name ?? null,
  )
  const elsewhere = issue.objectRef.canvasId !== activeCanvasId && canvasName
  const [techOpen, setTechOpen] = useState(false)
  const { hot, bind } = useHot()
  const objectId = issue.objectRef.objectId
  const point = (on: boolean) => {
    if (!objectId) return
    useUiStore.getState().setIssueHover(on ? { objectId, gid: issue.objectRef.gid ?? null } : null)
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
    onFocus: () => {
      bind.onFocus()
      point(true)
    },
    onBlur: (e: FocusEvent) => {
      bind.onBlur(e)
      point(false)
    },
  }
  const valueText = values.current
    ? values.expected
      ? pr('valueArrow', { current: values.current, expected: values.expected })
      : values.current
    : null
  return (
    <li className="relative">
      <button
        type="button"
        {...rowBind}
        data-issue-row
        // 稳定的机器标识（规则码 + 画布对象 id），新手教程按它找那一行；aria-label 是本地化文案，不能当选择器
        data-issue-rule={issue.ruleCode}
        data-issue-object={objectId ?? undefined}
        aria-current={current ? 'true' : undefined}
        onClick={onLocate}
        aria-label={issueAriaLabel(issue)}
        title={issueDetailText(issue)}
        style={treeIndent(depth)}
        className={cn(listRowClass({ size: 'sm', selected: current }), TRAIL_PAD, 'w-[calc(100%-0.5rem)] text-left')}
      >
        <TreeChevron />
        <TruncateMiddle text={subjectName(issue)} className="min-w-0 shrink" />
        {/* 没有「当前 → 要求」的规则（出界、缺字体……）：说明跟在名字后面，一行截断，全文在 title 与 ⓘ 里 */}
        {!valueText && <span className={cn(rowMetaClass(current), 'min-w-0 flex-1 truncate')}>{issueDetailText(issue)}</span>}
        {elsewhere && (
          <span className={cn(rowMetaClass(current), 'min-w-0 shrink-[2] truncate')}>{pr('onCanvas', { name: canvasName })}</span>
        )}
      </button>
      <TrailCell
        hot={hot || current || techOpen}
        bind={rowBind}
        rest={
          valueText ? (
            <span data-issue-values className={cn(rowMetaClass(current), 'truncate')} title={valueText}>
              {values.current}
              {values.expected && (
                <>
                  <span aria-hidden className="text-ink-faint">
                    {' → '}
                  </span>
                  <span className={current ? 'text-ink' : 'text-ink-2'}>{values.expected}</span>
                </>
              )}
            </span>
          ) : null
        }
        action={
          <>
            <FixButton issue={issue} variant="ghost" />
            <TechPopover issue={issue} open={techOpen} onOpenChange={setTechOpen} />
          </>
        }
      />
    </li>
  )
}

/**
 * 技术详情（gid、属性、检查项码）：普通用户一辈子不用打开它，排障的人一定找得到——尾随格里的 ⓘ，
 * 一个 popover（2026-10-07 设计审计 §9.4：此前在行下展开，逐行扫视时每行涨约 20px）。
 */
function TechPopover({
  issue,
  open,
  onOpenChange,
}: {
  issue: ValidationIssue
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Popover
      open={open}
      onOpenChange={onOpenChange}
      width={260}
      ariaLabel={pr('techTitle')}
      trigger={
        <IconButton iconSize="xs" label={pr('techTitle')} data-issue-tech-toggle className="text-ink-3 hover:text-ink-2">
          <Info size={ICON_SIZE.sm} />
        </IconButton>
      }
    >
      <p className="type-caption mb-1 px-1">{issueDetailText(issue)}</p>
      <ul data-issue-tech aria-label={pr('techTitle')} className="flex flex-col gap-0.5 px-1 pb-0.5">
        {technicalDetailLines(issue).map((line) => (
          <li key={line} className="break-all font-mono text-xs leading-4 text-ink-3">
            {line}
          </li>
        ))}
      </ul>
    </Popover>
  )
}
