import { useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, CircleCheck, ClipboardList, ShieldAlert } from 'lucide-react'
import { t as translate } from '@/i18n'
import { focusFailureMessage, focusIssue } from '@/lib/issueFocus'
import { fixOptions } from '@/lib/issueFix'
import { resolveDocumentSpec } from '@/lib/specBinding'
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
import { applyIssueFix, applyIssueFixes } from '@/store/issueFixActions'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { schedule, useValidationStore } from '@/store/validationStore'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'

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
 * 接入状态（哪张图连没连上脚本）刻意**不混进来**：那是另一类事实，有自己的
 * 中心与自己的下一步；底部只放一条链接把用户送过去。
 */
export function ProblemPanel() {
  useTranslation(['errors', 'workspace'])
  const issues = useValidationStore((s) => s.issues)
  const ready = useValidationStore((s) => s.ready)
  const failed = useValidationStore((s) => s.failed)
  const running = useValidationStore((s) => s.running)
  const filter = useUiStore((s) => s.problemFilter)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const listRef = useRef<HTMLUListElement>(null)

  const counts = useMemo(() => {
    const out: Record<Severity, number> = { error: 0, warn: 0, not_verifiable: 0, suggestion: 0 }
    for (const i of issues) out[i.severity] += 1
    return out
  }, [issues])

  const shown = useMemo(() => {
    const keep = filter?.length ? new Set(filter) : null
    const rank = (s: Severity) => SEVERITIES.indexOf(s)
    return issues
      .filter((i) => !keep || keep.has(i.severity))
      .slice()
      .sort((a, b) => rank(a.severity) - rank(b.severity))
  }, [issues, filter])

  const fixableHere = useMemo(
    () => shown.filter((i) => i.fixKind === 'safe_auto' && i.objectRef.canvasId === activeCanvasId),
    [shown, activeCanvasId],
  )

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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-1 px-3 pb-2">
        {SEVERITIES.filter((s) => counts[s] > 0).map((s) => (
          <SeverityChip key={s} severity={s} count={counts[s]} active={!!filter?.includes(s)} />
        ))}
        <span className="flex-1" />
        {fixableHere.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            className="text-xs"
            onClick={() => runBatchFix(fixableHere)}
          >
            {pr('fixAll', { count: fixableHere.length })}
          </Button>
        )}
      </div>

      {failed ? (
        <EmptyState
          icon={ShieldAlert}
          title={pr('failedTitle')}
          /* 「查不了」与「没问题」是两个答案：压成一个的话用户会带着一屏
             静悄悄的绿去投稿 */
          hint={pr(ready ? 'failedKeptHint' : 'failedHint')}
          action={{ label: pr('retry'), onClick: () => schedule() }}
        />
      ) : !ready && running ? (
        <p className="px-3 py-6 text-center text-xs text-ink-3">{pr('running')}</p>
      ) : shown.length === 0 ? (
        <EmptyState
          icon={CircleCheck}
          title={issues.length ? pr('noneInFilter') : pr('none')}
          hint={issues.length ? undefined : pr('noneHint')}
          action={
            issues.length
              ? { label: pr('clearFilter'), onClick: () => useUiStore.getState().setProblemFilter(null) }
              : undefined
          }
        />
      ) : (
        <ul
          ref={listRef}
          onKeyDown={roam}
          aria-label={pr('listLabel')}
          className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2"
        >
          {shown.map((issue) => (
            <IssueRow key={issue.issueId} issue={issue} activeCanvasId={activeCanvasId} />
          ))}
        </ul>
      )}

      <ReadinessLink />
    </div>
  )
}

function SeverityChip({
  severity,
  count,
  active,
}: {
  severity: Severity
  count: number
  active: boolean
}) {
  const Icon = SEVERITY_ICON[severity]
  const label = severityLabel(severity)
  const toggle = () => {
    const cur = useUiStore.getState().problemFilter ?? []
    const next = cur.includes(severity) ? cur.filter((s) => s !== severity) : [...cur, severity]
    useUiStore.getState().setProblemFilter(next.length ? next : null)
  }
  return (
    <button
      onClick={toggle}
      aria-pressed={active}
      aria-label={pr('filterAria', { label, count })}
      className={cn(
        'flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs outline-none',
        'transition-colors focus-visible:focus-ring',
        active ? 'border-accent bg-accent-subtle text-accent' : 'border-border text-ink-2 hover:bg-ink/[.05]',
      )}
    >
      <Icon size={11} className={cn('shrink-0', toneOf(severity))} aria-hidden />
      <span>{label}</span>
      <span className="font-mono text-ink-3">{count}</span>
    </button>
  )
}

/** 等级配色。**颜色不是唯一表达**：图标形状与文字标签各说一遍同一件事。 */
const toneOf = (s: Severity): string =>
  s === 'error' ? 'text-danger' : s === 'suggestion' ? 'text-ink-faint' : 'text-ink-3'

function IssueRow({
  issue,
  activeCanvasId,
}: {
  issue: ValidationIssue
  activeCanvasId: string
}) {
  const Icon = SEVERITY_ICON[issue.severity]
  const values = issueValues(issue)
  const canvasName = useDocumentStore(
    (s) => s.canvases.find((c) => c.id === issue.objectRef.canvasId)?.name ?? null,
  )
  const elsewhere = issue.objectRef.canvasId !== activeCanvasId
  const locate = () => {
    const outcome = focusIssue(issue)
    if (!outcome.ok) useUiStore.getState().setStatus(focusFailureMessage(outcome.reason), 'error')
  }
  return (
    <li className="rounded-sm px-1.5 py-1 hover:bg-ink/[.035]">
      <div className="flex items-start gap-1">
        {/* 整行点击 = 定位。修复是它的兄弟节点而不是子节点——按钮套按钮
            在辅助技术里是一个读不出来的控件（nested interactive） */}
        <button
          data-issue-row
          onClick={locate}
          aria-label={issueAriaLabel(issue)}
          title={issueDetailText(issue)}
          className="flex min-w-0 flex-1 items-start gap-1.5 rounded-sm p-0.5 text-left outline-none focus-visible:focus-ring"
        >
          <Icon size={12} className={cn('mt-0.5 shrink-0', toneOf(issue.severity))} aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-baseline gap-1.5">
              <span className="min-w-0 truncate text-xs text-ink">{issueTitle(issue)}</span>
              {values.current && (
                <span className="shrink-0 font-mono text-[10px] text-ink-3">
                  {values.expected
                    ? pr('valueArrow', { current: values.current, expected: values.expected })
                    : values.current}
                </span>
              )}
            </span>
            <span className="block truncate text-[11px] text-ink-3">
              {subjectName(issue)}
              {elsewhere && canvasName ? pr('onCanvas', { name: canvasName }) : ''}
            </span>
          </span>
        </button>
        <FixButton issue={issue} />
      </div>
      <TechnicalDetails issue={issue} />
    </li>
  )
}

function FixButton({ issue }: { issue: ValidationIssue }) {
  // **订阅 `specs`，不订阅 `catalog()`**：后者每次调用都新建一个数组，
  // 拿它当 zustand 选择器的返回值 = 每一帧都"变了" = 无限重渲染
  const specs = useProfileStore((s) => s.specs)
  const doc = useDocumentStore((s) => s.doc)
  const profile = useMemo(
    () => resolveDocumentSpec(doc.profile, toCatalog(specs)).profile,
    [doc.profile, specs],
  )
  if (issue.fixKind === 'none') return null
  if (issue.fixKind === 'safe_auto') {
    return (
      <Button size="sm" className="shrink-0 text-xs" onClick={() => runFix(issue)}>
        {pr('fix')}
      </Button>
    )
  }
  const options = fixOptions(issue, profile)
  return (
    <Menu
      width={180}
      trigger={
        <Button size="sm" className="shrink-0 text-xs">
          {pr('fixChoose')}
        </Button>
      }
    >
      {options.map((o) => (
        <MenuItem key={o.choice} onSelect={() => runFix(issue, o.choice)}>
          {pr(`fixOption.${o.labelKey}`, o.params)}
        </MenuItem>
      ))}
    </Menu>
  )
}

/** 技术详情默认收起：普通用户一辈子不用打开它，排障的人一定找得到。 */
function TechnicalDetails({ issue }: { issue: ValidationIssue }) {
  const lines = technicalDetailLines(issue)
  return (
    <details className="group ml-5 mt-0.5">
      {/* `ink-faint` 只给装饰与禁用态：这是个真控件、上面是要读的字，
          用它量出来 2.54:1（axe serious，e2e 那条门禁当场红） */}
      <summary className="flex cursor-default list-none items-center gap-0.5 text-[11px] text-ink-3 outline-none focus-visible:focus-ring">
        <ChevronRight
          size={10}
          aria-hidden
          className="shrink-0 transition-transform group-open:rotate-90"
        />
        {pr('techTitle')}
      </summary>
      <ul className="mt-0.5 flex flex-col gap-0.5">
        {lines.map((line) => (
          <li key={line} className="break-all font-mono text-[10px] leading-relaxed text-ink-3">
            {line}
          </li>
        ))}
      </ul>
    </details>
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
          onClick={() => useProjectReadinessStore.getState().openCenter()}
          className="flex w-full items-center gap-1.5 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
        >
          <ClipboardList size={12} className="shrink-0 text-ink-3" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{pr('readiness', { count: pending })}</span>
          <ChevronRight size={11} className="shrink-0 text-ink-3" aria-hidden />
        </button>
      </Tip>
    </div>
  )
}

/* -------------------------------- 动作 ------------------------------------ */

function currentProfile() {
  const doc = useDocumentStore.getState().doc
  return resolveDocumentSpec(doc.profile, useProfileStore.getState().catalog()).profile
}

function runFix(issue: ValidationIssue, choice?: string): void {
  const res = applyIssueFix(issue, currentProfile(), choice)
  const ui = useUiStore.getState()
  if (res.ok) ui.setStatus({ key: 'problems.fixed', ns: 'errors', values: { count: res.applied } })
  else ui.setStatus({ key: `problems.fixFailed.${res.reason}`, ns: 'errors' }, 'error')
}

function runBatchFix(issues: ValidationIssue[]): void {
  const res = applyIssueFixes(issues, currentProfile())
  const ui = useUiStore.getState()
  if (res.ok) ui.setStatus({ key: 'problems.fixed', ns: 'errors', values: { count: res.applied } })
  else ui.setStatus({ key: `problems.fixFailed.${res.reason}`, ns: 'errors' }, 'error')
}
