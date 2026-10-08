/**
 * 导入即扫描（T02）的快照 → 人话 + 「这条要不要出现」。**唯一的一份**。
 *
 * 两条纪律（与 `readinessText.ts` 同源）：
 *
 * 1. **按后端给的枚举查句子**（outcome / issue code / role），不自己从原始计数里推结论；
 * 2. **不暴露实现术语**（registry / AST / probe / symlink）。账本里的 code 在这里翻成「某个目录读不动」
 *    「有个云盘占位文件没有读」这样的话；精确路径只作为插值出现在详情里。
 *
 * 「要不要出现」也在这一处：扫描完整且没有任何需要用户注意的事（静态项目、脚本都已连接、空项目）
 * **不出现**——一个什么都没发现的检查不该每次打开项目都占一行；但用户主动「重新打开」时照常显示。
 */
import { t as translate } from '@/i18n'
import type { ProjectScan, ProjectScanIssue } from '@/lib/api'

/** 看不全的账本里有没有「真的有东西没看见」的条目（`note` 是设计内的静默剪枝） */
export const hasPartialIssues = (scan: ProjectScan): boolean =>
  scan.issues.some((i) => i.severity === 'partial')

/** 这一轮扫描此刻有没有值得提示用户的事（不含「用户主动重新打开」） */
export function scanNeedsAttention(scan: ProjectScan, slow: boolean): boolean {
  switch (scan.state) {
    case 'running':
      return slow // 更快的（静态小项目）不闪一下
    case 'failed':
    case 'cancelled':
      return true
    default:
      break
  }
  const kind = scan.outcome.kind
  if (kind === 'target_found' || kind === 'choose_target' || kind === 'unchecked') return true
  return hasPartialIssues(scan)
}

/** 条要不要显示。`dismissedScanId` 是被关掉的那一轮；`forced` 是用户主动重新打开。 */
export function scanBarVisible(
  scan: ProjectScan | null,
  s: { dismissedScanId: string | null; forced: boolean; slow: boolean },
): boolean {
  if (!scan) return false
  if (s.dismissedScanId === scan.scan_id && !s.forced) return false
  return s.forced || scanNeedsAttention(scan, s.slow)
}

const sc = (key: string, v?: Record<string, unknown>): string =>
  translate(`scan.${key}`, { ns: 'workspace', ...v })

/** 条上的那一句话 */
export function scanLine(scan: ProjectScan): string {
  const found = scan.found ?? { scripts: scan.budget.scripts, assets: scan.budget.assets }
  const kind = scan.outcome.kind
  const base = sc(`line.${kind}`, {
    scripts: found.scripts,
    assets: found.assets,
    script: scan.default_target ?? '',
    count: scan.targets?.filter((t) => t.role !== 'auxiliary').length ?? 0,
    issues: scan.issues.filter((i) => i.severity === 'partial').length,
  })
  // 有目标时还要把「没看全」说出来：目标是真的，但「只有这一个」不能下结论
  if (kind !== 'scanning' && kind !== 'unchecked' && hasPartialIssues(scan)) {
    return `${base} ${sc('partialNote')}`
  }
  return base
}

/** 账本里一条的人话 */
export function issueLine(issue: ProjectScanIssue): string {
  return sc(`issue.${issue.code}`, { path: issue.path ?? '', count: issue.count })
}

export const roleLabel = (role: 'plot' | 'auxiliary' | 'unknown'): string => sc(`role.${role}`)

export function environmentLine(scan: ProjectScan): string {
  const n = scan.environment?.candidates.length ?? 0
  return n > 0 ? sc('env.some', { count: n }) : sc('env.none')
}

export function dependenciesLine(scan: ProjectScan): string | null {
  const deps = scan.dependencies
  if (!deps || deps.files.length === 0) return null
  return sc('deps.declared', { files: deps.files.join('、'), count: deps.requirements })
}
