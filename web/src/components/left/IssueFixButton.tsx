import { useMemo } from 'react'
import { t as translate } from '@/i18n'
import { fixOptions } from '@/lib/issueFix'
import { resolveDocumentSpec } from '@/lib/specBinding'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { applyIssueFix } from '@/store/issueFixActions'
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
export function FixButton({ issue }: { issue: ValidationIssue }) {
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

/** 「这个项目按哪套规范检查」——只在 `lib/specBinding` 判，这里只是取一次 */
export function currentProfile() {
  const doc = useDocumentStore.getState().doc
  return resolveDocumentSpec(doc.profile, useProfileStore.getState().catalog()).profile
}

/** 修一条；成功 / 失败都用问题面板那两句 toast */
export function runFix(issue: ValidationIssue, choice?: string): void {
  const res = applyIssueFix(issue, currentProfile(), choice)
  const ui = useUiStore.getState()
  if (res.ok) ui.setStatus({ key: 'problems.fixed', ns: 'errors', values: { count: res.applied } })
  else ui.setStatus({ key: `problems.fixFailed.${res.reason}`, ns: 'errors' }, 'error')
}
