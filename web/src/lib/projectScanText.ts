/**
 * 导入即扫描（T02）的快照 → 人话 + 「引导卡要不要露出来」。**唯一的一份**。
 *
 * 两条纪律（与 `readinessText.ts` 同源）：
 *
 * 1. **按后端给的枚举查句子**（outcome / issue code / role），不自己从原始计数里推结论；
 * 2. **不暴露实现术语**（registry / AST / probe / symlink）。账本里的 code 在这里翻成「某个目录读不动」
 *    「有个云盘占位文件没有读」这样的话；精确路径只作为插值出现在详情里。
 *
 * 「要不要露出来」也在这一处（`scanCard`，T13b 引导卡）：扫描完整且没有任何需要用户注意的事（静态项目、脚本都已连接、
 * 空项目）**不出现**——一个什么都没发现的检查不该每次打开项目都弹一次；但用户主动「显示项目检查结果」时照常显示。
 */
import { t as translate } from '@/i18n'
import type { ProjectScan, ProjectScanIssue } from '@/lib/api'

/** 看不全的账本里有没有「真的有东西没看见」的条目（`note` 是设计内的静默剪枝） */
export const hasPartialIssues = (scan: ProjectScan): boolean =>
  scan.issues.some((i) => i.severity === 'partial')

/**
 * 引导卡（T13b）在**还没有准备会话**时，按这一轮扫描该露出什么：
 *
 * * `discover` / `choose`：发现了唯一的 / 多个待准备的绘图脚本——卡片（每个项目自动弹一次）；
 * * `scanning`：扫描跑得慢（超过 `SLOW_SCAN_MS`）——只出角标；
 * * `stuck`：没看全 / 失败 / 取消——角标，点开是一句话 +「重新检查」；
 * * `quiet`：没什么要说的（静态项目、脚本都已连接、空项目），只在用户主动「显示项目检查结果」时出现；
 * * `null`：什么都不露。
 */
export type ScanCardKind = 'discover' | 'choose' | 'scanning' | 'stuck' | 'quiet'

export function scanCard(
  scan: ProjectScan | null,
  s: { forced: boolean; slow: boolean },
): { kind: ScanCardKind; key: string; values: Record<string, unknown> } | null {
  if (!scan) return null
  const found = scan.found ?? { scripts: scan.budget.scripts, assets: scan.budget.assets }
  const targets = scan.targets?.filter((t) => t.role !== 'auxiliary').length ?? 0
  const values = { script: scan.default_target ?? '', count: targets, scripts: found.scripts, assets: found.assets }
  if (scan.state === 'running') return s.slow || s.forced ? { kind: 'scanning', key: 'scanning', values } : null
  if (scan.state === 'failed' || scan.state === 'cancelled') return { kind: 'stuck', key: scan.state, values }
  const kind = scan.outcome.kind
  if (kind === 'target_found') return { kind: 'discover', key: kind, values }
  if (kind === 'choose_target') return { kind: 'choose', key: kind, values }
  if (kind === 'unchecked' || hasPartialIssues(scan)) return { kind: 'stuck', key: 'unchecked', values }
  return s.forced ? { kind: 'quiet', key: kind, values } : null
}

const sc = (key: string, v?: Record<string, unknown>): string =>
  translate(`scan.${key}`, { ns: 'workspace', ...v })

/** 卡片 / 角标上的那一句（`scanCard` 给的键） */
export const scanLine = (card: { key: string; values: Record<string, unknown> }): string => sc(`line.${card.key}`, card.values)

/** 账本里一条的人话 */
export function issueLine(issue: ProjectScanIssue): string {
  return sc(`issue.${issue.code}`, { path: issue.path ?? '', count: issue.count })
}

export const roleLabel = (role: 'plot' | 'auxiliary' | 'unknown'): string => sc(`role.${role}`)

export function dependenciesLine(scan: ProjectScan): string | null {
  const deps = scan.dependencies
  if (!deps || deps.files.length === 0) return null
  return sc('deps.declared', { files: deps.files.join('、'), count: deps.requirements })
}
