/**
 * 「联系维护者」那一行的设置搜索登记（#1039）：只在中文界面可搜，与行本身只在中文界面显示同一个判据。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { searchSettings, type SectionId } from './settingsRegistry'

const ORDER: SectionId[] = ['diagnostics']
const hits = (q: string) =>
  searchSettings(q, ORDER, () => 'x').flatMap((r) => r.entries.map((e) => e.id))

afterEach(async () => {
  await setLocale('zh-CN')
})

describe('设置搜索：联系维护者', () => {
  it('zh-CN：搜「企业微信」「联系」「维护者」都落在这一行', async () => {
    await setLocale('zh-CN')
    for (const q of ['企业微信', '联系', '维护者']) expect(hits(q), q).toContain('diagnostics.contact')
  })

  it('en-US：搜不到它（中英文关键词都不行）', async () => {
    await setLocale('en-US')
    for (const q of ['企业微信', '联系', '维护者', 'wecom', 'contact', 'maintainer'])
      expect(hits(q), q).not.toContain('diagnostics.contact')
  })
})
