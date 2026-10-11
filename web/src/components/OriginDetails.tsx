import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/Badge'
import {
  coverageCodeText,
  coverageDetails,
  distFailureText,
  originReasonText,
} from '@/lib/importOrigin'
import type { EnvCoverageDetail, JointOrigin, PreparationOriginFacts } from '@/lib/api'
import { t as translate } from '@/i18n'

/**
 * Import Origin Resolver 的**折叠详情**里的几块列表（PR6）。它们只被放进默认收起的「详情」——卡片 / 对话框默认可见区只有
 * 一句话和至多一个主按钮（用户硬性要求）。这里全是翻译：原因码、覆盖度码、核验原因码各一句人话，名字与计数来自后端。
 */
//: 文案键写成字面量（`og` 的前缀是 `prep.origin.`）
const og = (key: string, v?: Record<string, unknown>) => translate(`prep.origin.${key}`, { ns: 'workspace', ...v })

/** 准备卡详情：来源未定的 import 按原因各一行（计数 + 一句人话）。后端只给计数与闭集原因码，不给名字 */
export function OriginReasonCounts({ facts }: { facts: PreparationOriginFacts }) {
  useTranslation('workspace')
  const rows = Object.entries(facts.by_reason).sort(([a], [b]) => (a < b ? -1 : 1))
  if (rows.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-origin-reasons>
      <p>{og('detail.listIntro')}</p>
      <ul className="flex flex-col gap-0.5">
        {rows.map(([reason, n]) => (
          <li key={reason} data-origin-reason={reason}>
            <span className="tabular-nums text-ink-3">{n}</span> · {originReasonText(reason)}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 对话框详情：联合计划里每个来源未定的 import（名字是用户自己的，不翻译）+ 它的原因一句人话 */
export function OriginEntryList({ origins, title }: { origins: JointOrigin[]; title: string }) {
  useTranslation('workspace')
  if (origins.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-origin-entries>
      <p>{title}</p>
      <ul className="flex flex-col gap-0.5">
        {origins.map((o) => (
          <li key={o.import_name} data-origin-entry={o.import_name} data-origin-reason={o.reason}>
            <span className="font-mono text-ink-2">{o.import_name}</span> · {originReasonText(o.reason)}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 候选环境对脚本要的库的覆盖度：每个 detail 码一句人话（模块级的带上库名） */
export function CoverageDetailList({ detail }: { detail: EnvCoverageDetail[] | undefined }) {
  useTranslation('workspace')
  const rows = coverageDetails(detail)
  if (rows.length === 0) return null
  return (
    <ul className="flex flex-col gap-0.5" data-origin-coverage>
      {rows.map((d, i) => (
        <li key={`${d.code}:${d.module ?? ''}:${i}`} data-origin-coverage-code={d.code}>
          {d.module && <span className="font-mono text-ink-2">{d.module} · </span>}
          {coverageCodeText(d.code)}
        </li>
      ))}
    </ul>
  )
}

/** 装后核验没过的原因（进度终态的 `dist_reasons`，闭集码）：每个一句人话 */
export function DistFailureList({ reasons }: { reasons: readonly string[] }) {
  useTranslation('workspace')
  if (reasons.length === 0) return null
  return (
    <ul className="flex flex-col gap-0.5" data-origin-dist-reasons>
      {reasons.map((r) => (
        <li key={r} data-origin-dist-reason={r}>
          {distFailureText(r)}
        </li>
      ))}
    </ul>
  )
}

/** 「用户指定、未经可信来源核实」的小标（确认页每个包旁边；文字来自 `impact.user_specified[].trust` 这个事实） */
export function UserSpecifiedBadge() {
  useTranslation('workspace')
  return (
    <Badge tone="warn" data-origin-trust="user_specified_unverified">
      {og('detail.userSpecifiedBadge')}
    </Badge>
  )
}
