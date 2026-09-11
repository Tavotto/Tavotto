import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, ChevronUp, CircleCheck, ClipboardList, TriangleAlert, X } from 'lucide-react'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { focusFailureMessage, focusIssue } from '@/lib/issueFocus'
import {
  cursorFor,
  cursorView,
  groupIssues,
  type IssueGroup,
  type ProblemScope,
} from '@/lib/problemList'
import { cn } from '@/lib/utils'
import { SEVERITIES, type Severity } from '@/lib/profile'
import {
  issueAriaLabel,
  issueDetailText,
  issueTitle,
  issueValues,
  severityLabel,
  SEVERITY_ICON,
  subjectName,
  technicalDetailLines,
} from '@/lib/validationText'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { applyIssueFixes } from '@/store/issueFixActions'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { schedule, useValidationStore } from '@/store/validationStore'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { currentProfile, FixButton } from './IssueFixButton'
import { Segmented } from '../ui/Segmented'
import { Tip } from '../ui/Tooltip'
import { useScopedProblems } from './useProblemScope'

/** 本组文案在 errors:problems.* 下（问题的措辞与检查项同一个命名空间） */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/**
 * 左侧「问题」抽屉。**打开导出对话框才知道图有没有问题的日子到此为止。**
 *
 * 这一屏只做三件事：说清有什么问题、点一下跳到那个真实对象、能安全修的给
 * 一颗按钮。它**不自己跑检查**（`store/validationStore.ts` 唯一驱动）、
 * **不自己挑规范**（`lib/specBinding.ts` 唯一判据）、**不显示 gid**
 * （精确名词只在每行的技术详情里）。
 *
 * ### 呈现（审计 T09；2026-09-11 按用户给的参考稿重排）
 *
 * * **范围**：「当前图 / 整个文档」两档等分的页签。判据在 `lib/problemList.ts`，
 *   抽屉标题的计数与这里同一份。轨道角标仍是全文档数——它是入口，不跟着范围变。
 * * **概览条**：三格并排的等级计数（色点 + 名字 + 数），点一格 = 只看这一级；
 *   下面一行说「当前画布可自动修复 N 项」并给一颗按钮。
 * * **按规则聚合**：组头 = 等级角标 + 标题 + 「N 个对象」+ 该组的「全部修复」，
 *   滚动时钉在顶上；组内一行一个真实对象，只说「谁、现在多少 → 要多少」，
 *   左侧一条竖线把它们挂在组头下面。标题在组头说一遍，不再逐行重复、也不截断。
 * * **定位后清单留在原地**：`issueFocus` 不再让元素树顶掉左栏；正在处理的那一条
 *   带浅灰底 + 「当前」字样（不只靠颜色），底部给「上一项 / 下一项」。修好一条
 *   它会消失，「下一项」指向顶上来的那一条。
 *
 * 接入状态（哪张图连没连上脚本）刻意**不混进来**：那是另一类事实，有自己的
 * 中心与自己的下一步；底部只放一条链接把用户送过去。
 */
export function ProblemPanel() {
  useTranslation(['errors', 'workspace'])
  const all = useValidationStore((s) => s.issues)
  const ready = useValidationStore((s) => s.ready)
  const failed = useValidationStore((s) => s.failed)
  const filter = useUiStore((s) => s.problemFilter)
  const cursor = useUiStore((s) => s.problemCursor)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const { figureId, figureName, scope, issues } = useScopedProblems()
  const listRef = useRef<HTMLUListElement>(null)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set())

  /**
   * 这一轮检查失败了，**但上一轮的结果被留着**（`validationStore` 刻意保留，
   * 见 web/AGENTS.md）。这时候清单要照常列——它们仍然算在计数条与导出摘要里，
   * 藏起来等于让用户看得见数字却找不到东西。
   */
  const retained = failed && ready && all.length > 0

  const counts = useMemo(() => {
    const out: Record<Severity, number> = { error: 0, warn: 0, not_verifiable: 0, suggestion: 0 }
    for (const i of issues) out[i.severity] += 1
    return out
  }, [issues])

  const shown = useMemo(() => {
    const keep = filter?.length ? new Set(filter) : null
    return issues.filter((i) => !keep || keep.has(i.severity))
  }, [issues, filter])

  const groups = useMemo(() => groupIssues(shown), [shown])
  const view = useMemo(() => cursorView(groups, cursor), [groups, cursor])

  const fixableHere = useMemo(
    () => shown.filter((i) => i.fixKind === 'safe_auto' && i.objectRef.canvasId === activeCanvasId),
    [shown, activeCanvasId],
  )

  // 清单空了，「正在处理第几条」就没有主语了（全修好 / 换了文档）
  useEffect(() => {
    if (cursor && groups.length === 0) useUiStore.getState().setProblemCursor(null)
  }, [cursor, groups.length])

  /** 定位 + 记下「正在处理这一条」。失败照旧说原因，游标不动。 */
  const locate = (issue: ValidationIssue) => {
    const outcome = focusIssue(issue)
    if (!outcome.ok) {
      useUiStore.getState().setStatus(focusFailureMessage(outcome.reason), 'error')
      return
    }
    useUiStore.getState().setProblemCursor(cursorFor(groups, issue.issueId))
  }

  /** 方向键在行间漫游：清单可能很长，只有 Tab 的话走到底要按几十次 */
  const roam = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    const rows = [...(listRef.current?.querySelectorAll<HTMLElement>('[data-issue-row]') ?? [])]
    if (!rows.length) return
    const at = rows.findIndex((r) => r.contains(document.activeElement))
    const next = at < 0 ? 0 : at + (e.key === 'ArrowDown' ? 1 : -1)
    if (next < 0 || next >= rows.length) return
    e.preventDefault()
    rows[next].focus()
  }

  const toggleGroup = (ruleCode: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(ruleCode)) next.delete(ruleCode)
      else next.add(ruleCode)
      return next
    })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ScopeBar figureId={figureId} figureName={figureName} scope={scope} />

      {/* 概览条只在这一轮结果就绪后出现：还在检查时挂着一条计数，与下面的
          「正在检查…」是两句互相打架的话（2026-09-11 设计包） */}
      {ready && issues.length > 0 && (
        <section className="shrink-0 border-b border-border px-3 pb-3 pt-3">
          <div
            role="group"
            aria-label={pr('severityLabel')}
            className="flex overflow-hidden rounded-md border border-border bg-surface-2/60"
          >
            {SEVERITIES.filter((s) => counts[s] > 0).map((s) => (
              <SeverityStat key={s} severity={s} count={counts[s]} active={!!filter?.includes(s)} />
            ))}
          </div>
          {fixableHere.length > 0 && (
            <div className="mt-2.5 flex items-center justify-between gap-3">
              <span className="min-w-0 text-xs text-ink-2">
                {pr('fixableHere', { count: fixableHere.length })}
              </span>
              <Button
                size="sm"
                variant="outline"
                className="shrink-0 text-xs"
                onClick={() => runBatchFix(fixableHere)}
              >
                {pr('fixAuto')}
              </Button>
            </div>
          )}
        </section>
      )}

      {/*
        这一轮查砸了、但上一轮的结果**留着**（`ready && issues.length`）：
        那就把失败说出来，**同时把留下来的问题继续列出来**。整屏换成一张错误
        空态的话，那些问题仍然被计进上面的计数条、也仍然进导出摘要，却在**唯一
        一份完整问题清单**里翻不到、点不到、跳不过去（PR #214 第七轮评审）。
      */}
      {retained && (
        <div
          role="status"
          className="mx-3 my-2 flex shrink-0 items-center gap-2 rounded-sm border border-warn/30 bg-warn-subtle px-2 py-1.5 text-xs leading-relaxed text-ink-2"
        >
          <TriangleAlert size={ICON_SIZE.sm} className="shrink-0 text-warn" aria-hidden />
          <span className="flex-1">{pr('failedKeptHint')}</span>
          <Button size="sm" variant="outline" className="h-6 text-xs" onClick={() => schedule()}>
            {pr('retry')}
          </Button>
        </div>
      )}

      {failed && !retained ? (
        <EmptyState
          icon={TriangleAlert}
          title={pr('failedTitle')}
          /* 「查不了」与「没问题」是两个答案：压成一个的话用户会带着一屏
             静悄悄的绿去投稿 */
          hint={pr(ready ? 'failedKeptHint' : 'failedHint')}
          action={{ label: pr('retry'), onClick: () => schedule() }}
        />
      ) : !ready ? (
        /* **判据是 `!ready`，不是 `!ready && running`。** 换文档之后
           `resetValidation()` 与那一轮真正开跑之间有 250ms 防抖窗口，
           那段时间里 `ready=false, running=false, issues=[]` —— 挂着
           `running` 的话会**掉进下面那个绿色的"没有问题"**，而这一刻
           根本还没查过（T-54，PR #214 第六轮评审）。 */
        <p className="px-3 py-6 text-center text-xs text-ink-3">{pr('running')}</p>
      ) : shown.length === 0 ? (
        all.length === 0 ? (
          <EmptyState icon={CircleCheck} title={pr('none')} hint={pr('noneHint')} />
        ) : issues.length === 0 ? (
          /* 范围裁掉了：整个文档里有问题、这张图上没有——是两句不同的话 */
          <EmptyState
            icon={CircleCheck}
            title={pr('noneInScope')}
            hint={pr('noneInScopeHint', { count: all.length })}
            action={{
              label: pr('scopeDocument'),
              onClick: () => useUiStore.getState().setProblemScope('document'),
            }}
          />
        ) : (
          <EmptyState
            icon={CircleCheck}
            title={pr('noneInFilter')}
            action={{ label: pr('clearFilter'), onClick: () => useUiStore.getState().setProblemFilter(null) }}
          />
        )
      ) : (
        <ul
          ref={listRef}
          onKeyDown={roam}
          aria-label={pr('listLabel')}
          className="min-h-0 flex-1 overflow-y-auto px-3 pb-3"
        >
          {groups.map((g) => (
            <GroupBlock
              key={g.ruleCode}
              group={g}
              open={!collapsed.has(g.ruleCode)}
              onToggle={() => toggleGroup(g.ruleCode)}
              currentId={view.current?.issueId ?? null}
              activeCanvasId={activeCanvasId}
              onLocate={locate}
            />
          ))}
        </ul>
      )}

      {cursor && groups.length > 0 && <CursorBar view={view} onLocate={locate} />}
      <ReadinessLink />
    </div>
  )
}

/* ------------------------------- 范围 ------------------------------------- */

/**
 * 「当前图 / 整个文档」两档等分的页签。没有当前图时那一档留在原位灰掉、
 * 说明为什么——消失的选项解释不了自己。
 */
function ScopeBar({
  figureId,
  figureName,
  scope,
}: {
  figureId: string | null
  figureName: string | null
  scope: ProblemScope
}) {
  return (
    <div className="shrink-0 px-3">
      <Segmented<ProblemScope>
        ariaLabel={pr('scopeLabel')}
        tone="quiet"
        size="md"
        value={scope}
        onChange={(v) => useUiStore.getState().setProblemScope(v)}
        items={[
          {
            value: 'figure',
            label: pr('scopeFigure'),
            disabled: !figureId,
            title: figureId
              ? figureName
                ? pr('scopeFigureTip', { name: figureName })
                : undefined
              : pr('scopeFigureUnavailable'),
          },
          { value: 'document', label: pr('scopeDocument') },
        ]}
      />
      {/* 图名就在范围下面：用户得知道「当前图」指的是谁 */}
      {scope === 'figure' && figureName && (
        <p className="truncate pt-1.5 text-[11px] text-ink-3" title={figureName}>
          {figureName}
        </p>
      )}
    </div>
  )
}

/** 等级色点。**颜色不是唯一表达**：名字与数字各说一遍同一件事。 */
const DOT: Record<Severity, string> = {
  error: 'bg-danger',
  warn: 'bg-warn',
  not_verifiable: 'bg-ink-3',
  suggestion: 'bg-ink-faint',
}

/** 组头的等级角标：淡底 + 同色图标 */
const BADGE: Record<Severity, string> = {
  error: 'bg-danger-subtle text-danger',
  warn: 'bg-warn-subtle text-warn',
  not_verifiable: 'bg-surface-2 text-ink-2',
  suggestion: 'bg-surface-2 text-ink-3',
}

function SeverityStat({
  severity,
  count,
  active,
}: {
  severity: Severity
  count: number
  active: boolean
}) {
  const label = severityLabel(severity)
  const toggle = () => {
    const cur = useUiStore.getState().problemFilter ?? []
    const next = cur.includes(severity) ? cur.filter((s) => s !== severity) : [...cur, severity]
    useUiStore.getState().setProblemFilter(next.length ? next : null)
  }
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={active}
      aria-label={pr('filterAria', { label, count })}
      className={cn(
        'flex min-w-0 flex-1 items-center gap-2 border-r border-border px-2.5 py-2 text-left outline-none',
        'transition-colors last:border-r-0 focus-visible:focus-ring',
        active ? 'bg-selected' : 'hover:bg-surface',
      )}
    >
      <span aria-hidden className={cn('h-[7px] w-[7px] shrink-0 rounded-full', DOT[severity])} />
      <span className="min-w-0 truncate text-xs text-ink-2">{label}</span>
      <span className="font-mono text-xs font-medium tabular-nums text-ink">{count}</span>
    </button>
  )
}

/* ------------------------------- 分组 ------------------------------------- */

/**
 * 一条规则一组。组头把「这是什么问题」说一遍（标题不截断、可换行）并钉在
 * 滚动区顶上；组内每行只说「谁、现在多少 → 要多少」+ 定位 + 修复。
 */
function GroupBlock({
  group,
  open,
  onToggle,
  currentId,
  activeCanvasId,
  onLocate,
}: {
  group: IssueGroup
  open: boolean
  onToggle: () => void
  currentId: string | null
  activeCanvasId: string
  onLocate: (issue: ValidationIssue) => void
}) {
  const Icon = SEVERITY_ICON[group.severity]
  const title = issueTitle(group.issues[0])
  const fixable = group.issues.filter(
    (i) => i.fixKind === 'safe_auto' && i.objectRef.canvasId === activeCanvasId,
  )
  return (
    <li data-issue-group={group.ruleCode} className="mb-1">
      <div className="sticky top-0 z-[1] flex items-center gap-2 bg-surface py-2.5">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-sm text-left outline-none focus-visible:focus-ring"
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-ink-2 transition-colors hover:bg-ink/[.05]">
            <ChevronRight
              size={ICON_SIZE.sm}
              aria-hidden
              className={cn('transition-transform', open && 'rotate-90')}
            />
          </span>
          <span
            aria-hidden
            className={cn(
              'flex h-6 w-6 shrink-0 items-center justify-center rounded-sm',
              BADGE[group.severity],
            )}
          >
            <Icon size={ICON_SIZE.sm} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-xs font-medium leading-snug text-ink">{title}</span>
            <span className="mt-px block text-[11px] text-ink-3">
              {pr('groupObjects', { count: group.objects })}
              {' · '}
              {severityLabel(group.severity)}
            </span>
          </span>
        </button>
        {fixable.length > 0 && (
          <Button size="sm" className="shrink-0 text-xs" onClick={() => runBatchFix(fixable)}>
            {pr('groupFixAll')}
          </Button>
        )}
      </div>
      {open && (
        <ul className="ml-3 border-l border-border pl-3">
          {group.issues.map((issue) => (
            <IssueRow
              key={issue.issueId}
              issue={issue}
              current={issue.issueId === currentId}
              activeCanvasId={activeCanvasId}
              onLocate={() => onLocate(issue)}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function IssueRow({
  issue,
  current,
  activeCanvasId,
  onLocate,
}: {
  issue: ValidationIssue
  current: boolean
  activeCanvasId: string
  onLocate: () => void
}) {
  const values = issueValues(issue)
  const canvasName = useDocumentStore(
    (s) => s.canvases.find((c) => c.id === issue.objectRef.canvasId)?.name ?? null,
  )
  const elsewhere = issue.objectRef.canvasId !== activeCanvasId
  return (
    <li
      className={cn(
        'rounded-md transition-colors',
        // 「当前」= 浅灰圆角块 + 文字标签，不画左侧竖条、不用蓝（2026-09-11 用户反馈）
        current ? 'bg-selected' : 'hover:bg-ink/[.035]',
      )}
    >
      <div className="flex items-start gap-2 py-2 pl-2 pr-1">
        {/* 整行点击 = 定位。修复是它的兄弟节点而不是子节点——按钮套按钮
            在辅助技术里是一个读不出来的控件（nested interactive） */}
        <button
          data-issue-row
          // 稳定的机器标识（规则码 + 画布对象 id），新手教程按它找那一行；
          // aria-label 是本地化文案，不能当选择器
          data-issue-rule={issue.ruleCode}
          data-issue-object={issue.objectRef.objectId ?? undefined}
          aria-current={current ? 'true' : undefined}
          onClick={onLocate}
          aria-label={issueAriaLabel(issue)}
          title={issueDetailText(issue)}
          className="min-w-0 flex-1 rounded-sm text-left outline-none focus-visible:focus-ring"
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate text-xs leading-snug text-ink">{subjectName(issue)}</span>
            {current && (
              <span className="shrink-0 rounded-[3px] bg-surface px-1 text-[10px] leading-4 text-ink-2">
                {pr('current')}
              </span>
            )}
          </span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-[11px] leading-snug text-ink-2">
            {values.current ? (
              values.expected ? (
                <>
                  <span className="tabular-nums">{values.current}</span>
                  <span aria-hidden className="text-ink-3">
                    →
                  </span>
                  <span className="font-medium tabular-nums text-ink">{values.expected}</span>
                </>
              ) : (
                <span className="tabular-nums">{values.current}</span>
              )
            ) : (
              <span className="min-w-0 truncate">{issueDetailText(issue)}</span>
            )}
            {elsewhere && canvasName && <span className="text-ink-3">{pr('onCanvas', { name: canvasName })}</span>}
          </span>
        </button>
        <FixButton issue={issue} />
      </div>
      <TechnicalDetails issue={issue} />
    </li>
  )
}

/** 技术详情默认收起：普通用户一辈子不用打开它，排障的人一定找得到。 */
// 折叠三角走 `components/ui/Details` 那一份（这里从前是自己拼的
// `<details>` + ChevronRight，与树、检查器的折叠箭头对不上）。
function TechnicalDetails({ issue }: { issue: ValidationIssue }) {
  const lines = technicalDetailLines(issue)
  return (
    <Details className="mb-1.5 ml-2">
      {/* `ink-faint` 只给装饰与禁用态：这是个真控件、上面是要读的字，
          用它量出来 2.54:1（axe serious，e2e 那条门禁当场红） */}
      <Summary className="cursor-default gap-0.5 text-[11px] text-ink-3">{pr('techTitle')}</Summary>
      <ul className="mt-0.5 flex flex-col gap-0.5">
        {lines.map((line) => (
          <li key={line} className="break-all font-mono text-[10px] leading-relaxed text-ink-3">
            {line}
          </li>
        ))}
      </ul>
    </Details>
  )
}

/* ------------------------------- 游标 ------------------------------------- */

/**
 * 「正在处理第几条」+ 上一项 / 下一项。那条修好消失之后这里说「已处理」，
 * 「下一项」指向顶上来的那条——清单不必重开、位置不必重找。
 */
function CursorBar({
  view,
  onLocate,
}: {
  view: ReturnType<typeof cursorView>
  onLocate: (issue: ValidationIssue) => void
}) {
  return (
    <div
      data-problem-cursor
      aria-label={pr('cursorLabel')}
      className="flex shrink-0 items-center gap-1 border-t border-border px-2 py-1"
    >
      <span className="min-w-0 flex-1 truncate text-xs text-ink-2">
        {view.current
          ? `${pr('cursorAt', { pos: view.position, total: view.total })} · ${subjectName(view.current)}`
          : pr('cursorDone', { count: view.total })}
      </span>
      <Tip label={pr('prev')} side="top">
        <Button
          size="icon-sm"
          aria-label={pr('prev')}
          disabled={!view.prev}
          onClick={() => {
            if (view.prev) onLocate(view.prev)
          }}
        >
          <ChevronUp size={ICON_SIZE.sm} />
        </Button>
      </Tip>
      <Button
        size="sm"
        variant="outline"
        className="text-xs"
        disabled={!view.next}
        onClick={() => {
          if (view.next) onLocate(view.next)
        }}
      >
        {pr('next')}
      </Button>
      <Tip label={pr('cursorClose')} side="top">
        <Button
          size="icon-sm"
          aria-label={pr('cursorClose')}
          onClick={() => useUiStore.getState().setProblemCursor(null)}
        >
          <X size={ICON_SIZE.sm} />
        </Button>
      </Tip>
    </div>
  )
}

/**
 * 接入状态的出口。**不把就绪度问题混进上面的清单**——「这张图还没连上脚本」
 * 与「这张图字号偏小」的下一步完全不同，混在一起用户两件事都做不了。
 */
function ReadinessLink() {
  const report = useProjectReadinessStore((s) => s.report)
  if (!report || report.summary.total <= 0 || report.summary.editable >= report.summary.total) {
    return null
  }
  const pending = report.summary.total - report.summary.editable
  return (
    <div className="shrink-0 border-t border-border px-3 py-1.5">
      <Tip label={pr('readinessTip')} side="top">
        <button
          onClick={() => useProjectReadinessStore.getState().openCenter({ source: 'panel' })}
          className="flex w-full items-center gap-1.5 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
        >
          <ClipboardList size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{pr('readiness', { count: pending })}</span>
          <ChevronRight size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
        </button>
      </Tip>
    </div>
  )
}

/* -------------------------------- 动作 ------------------------------------ */

function runBatchFix(issues: ValidationIssue[]): void {
  const res = applyIssueFixes(issues, currentProfile())
  const ui = useUiStore.getState()
  if (res.ok) ui.setStatus({ key: 'problems.fixed', ns: 'errors', values: { count: res.applied } })
  else ui.setStatus({ key: `problems.fixFailed.${res.reason}`, ns: 'errors' }, 'error')
}
