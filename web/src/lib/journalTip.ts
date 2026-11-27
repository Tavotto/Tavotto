import { fieldLabel, sourceHost, valueText } from '@/lib/journalPick'
import type { JournalFieldSource } from '@/lib/journals'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'

/** 「问题」分段的取值（线缆 `journal.issue`，只在 type=wrong 时发） */
export const JOURNAL_ISSUES = ['wrong_value', 'updated', 'link_broken', 'other'] as const
export type JournalIssue = (typeof JOURNAL_ISSUES)[number]

/**
 * 「期刊线索」的上下文（ADR 0213）：用户从出处卡点「这条不对」、或在选择器里点「没有这本刊」时带进「联系作者」窗的那一份。
 * 全是目录数据（期刊 id / 规则键 / 目录当前值 / 出处 id / 目录版本）与搜索框里的字，**不含项目内容**；
 * 用户不填数值——能填的只有一句话、原文链接和附件。线缆形状见 `lib/api.ts` 的 `ContactJournalHead`。
 */
export interface JournalTipContext {
  type: 'wrong' | 'missing'
  journalId?: string
  /** 目录里的刊名（出处卡的主语），给灰字与主题用 */
  journalLabel?: string
  ruleKey?: string
  /** 规则的界面名（「线宽下限」），只给灰字用，不发给服务端 */
  ruleLabel?: string
  /** 目录里这一条的序列化值（≤200 字，引擎再校验一次）；反馈窗的「目录现值」也显示它 */
  current?: string
  /** 出处主机名与抓取日期，只给反馈窗的「当前出处」一行看，不发给服务端 */
  sourceHost?: string
  sourceDate?: string
  /** 出处原网页（出处卡「打开原网页」同一个地址），只给反馈窗里的主机名外链用，不发给服务端 */
  sourceUrl?: string
  sourceId?: string
  /** 选择器搜索框里的字（≤120 字） */
  query?: string
  catalog?: { v1?: number; v2?: number }
}

/** 同一条线索的识别键：同一条再点开时保留已写的草稿，换一条就换新的 */
export const journalTipKey = (c: JournalTipContext): string =>
  [c.type, c.journalId ?? '', c.ruleKey ?? '', c.query ?? ''].join('\u0000')

function catalogRevs(journalId: string | undefined): { v1?: number; v2?: number } | undefined {
  const v1 = useJournalCatalogStore.getState().catalog.catalog_rev
  const v2 = journalId ? useJournalV2Store.getState().byJournal[journalId]?.catalog_rev : undefined
  const out = { ...(v1 ? { v1 } : {}), ...(v2 ? { v2 } : {}) }
  return Object.keys(out).length ? out : undefined
}

/** 出处卡上某一条的线索上下文。 */
export function tipForSource(
  journalLabel: string,
  journalId: string | undefined,
  path: string,
  source: JournalFieldSource,
): JournalTipContext {
  return {
    type: 'wrong',
    journalId,
    journalLabel,
    ruleKey: path,
    ruleLabel: fieldLabel(path),
    current: valueText(path, source).slice(0, 200),
    sourceHost: sourceHost(source.url).replace(/^www\./, ''),
    sourceDate: source.retrieved || undefined,
    sourceUrl: source.link || undefined,
    // v2 折叠来源发 `v2:<事实 id>`（`src` 里有第二个冒号，过不了契约正则，也不是事实 id）；v1 照旧发 `src`
    sourceId: source.fact_id ? `v2:${source.fact_id}` : source.src,
    catalog: catalogRevs(journalId),
  }
}

/** 选择器搜不到时的线索上下文。 */
export function tipForMissing(query: string): JournalTipContext {
  return { type: 'missing', query: query.trim().slice(0, 120), catalog: catalogRevs(undefined) }
}
