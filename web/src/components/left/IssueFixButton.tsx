import { useMemo } from 'react'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { fixOptions } from '@/lib/issueFix'
import { resolveDocumentSpec } from '@/lib/specBinding'
import { cn } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import {
  applyIssueFix,
  applyIssueFixes,
  type BatchOptions,
  type FixFailure,
  type FixOutcome,
} from '@/store/issueFixActions'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'

/** 本组文案在 errors:problems.* 下（与问题面板同一个命名空间） */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/**
 * 「修复」按钮与它背后的动作——**问题面板与检查器里的就地提示共用同一份**。
 *
 * 从 `ProblemPanel` 里抽出来是因为第二个消费点出现了（审计 T14：落在被选元素
 * 上的问题就地显示，能修的给同一颗按钮）。各写一遍的后果是两处的成功 / 失败
 * 提示、`user_choice` 的菜单、规范的解析方式各自漂移。
 */
export function FixButton({ issue, className }: { issue: ValidationIssue; className?: string }) {
  // **订阅 `specs`，不订阅 `catalog()`**：后者每次调用都新建一个数组，
  // 拿它当 zustand 选择器的返回值 = 每一帧都"变了" = 无限重渲染
  const specs = useProfileStore((s) => s.specs)
  const doc = useDocumentStore((s) => s.doc)
  const profile = useMemo(
    () => resolveDocumentSpec(doc.profile, toCatalog(specs)).profile,
    [doc.profile, specs],
  )
  const fixing = useUiStore((s) => s.fixing)
  if (issue.fixKind === 'none') return null
  if (issue.fixKind === 'safe_auto') {
    return (
      <Button
        size="sm"
        className={cn('shrink-0', className)}
        disabled={fixing}
        onClick={() => void runFix(issue)}
      >
        {pr('fix')}
      </Button>
    )
  }
  const options = fixOptions(issue, profile)
  return (
    <Menu
      width={180}
      trigger={
        <Button size="sm" className={cn('shrink-0', className)} disabled={fixing}>
          {pr('fixChoose')}
        </Button>
      }
    >
      {options.map((o) => (
        <MenuItem key={o.choice} onSelect={() => void runFix(issue, o.choice)}>
          {pr(`fixOption.${o.labelKey}`, o.params)}
        </MenuItem>
      ))}
    </Menu>
  )
}

/** 修一条；结果用问题面板同一套措辞报出来。 */
export function runFix(issue: ValidationIssue, choice?: string): Promise<void> {
  return withBusy(() => applyIssueFix(issue, choice))
}

/**
 * 批量修：只修当前画布；「全部处理」不含建议档，组头的「全部修复」修的就是那一组
 * （集合由 `batchable()` 定，与计数同一份）。
 */
export function runBatchFix(issues: ValidationIssue[], opts?: BatchOptions): Promise<void> {
  return withBusy(() => applyIssueFixes(issues, opts))
}

async function withBusy(job: () => Promise<FixOutcome>): Promise<void> {
  const ui = useUiStore.getState()
  // 后端事务要真实渲染一两遍，几秒钟：先说一声，免得用户以为没点上又点一次
  ui.setFixing(true)
  ui.setStatus({ key: 'problems.fixing', ns: 'errors' })
  try {
    reportFix(await job())
  } finally {
    useUiStore.getState().setFixing(false)
  }
}

const fixFailedText = (f: FixFailure): UiMessage =>
  msg(`problems.fixFailed.${f.reason}`, f.font ? { font: f.font } : undefined, 'errors')

/**
 * 结果怎么说。**图动没动要说清楚**：没修成的那几条永远是「没改」，不是「改了一半」
 * ——后端裁决不过时整张图一个字不动。
 */
export function reportFix(res: FixOutcome): void {
  const ui = useUiStore.getState()
  const failedCount = res.failed.reduce((n, f) => n + f.count, 0)
  if (!res.ok) {
    ui.setStatus(fixFailedText(res.failed[0] ?? { reason: res.reason, count: 0 }), 'error')
    return
  }
  if (!failedCount) {
    ui.setStatus(msg('problems.fixed', { count: res.applied }, 'errors'))
    return
  }
  ui.setStatus(
    msg(
      'problems.fixedPartial',
      { count: res.applied, failed: failedCount, why: fixFailedText(res.failed[0]) },
      'errors',
    ),
  )
}
