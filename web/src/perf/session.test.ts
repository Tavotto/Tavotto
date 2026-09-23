/**
 * 探针编排（ADR 0075）的两件事，主语都是「拖动途中 documentStore 被谁动了」：
 *
 * 1. 通知分三类记：文档本体（`doc`）变了、保存状态（`saveState` / `dirty`）变了、别的。
 *    只数通知的话，上一次松手 1 秒后的自动保存改 saveState 会被读成「拖动途中写文档」
 *    ——2026-09-23 第一份真实报告就这么误报过。
 * 2. 自动保存的同步段有计时（`autosave.flush`），落在拖动途中时量得到它花了多久。
 *
 * 反证：把分类判据改成只看通知（三类都记成 doc）→ 第一条红；
 * 去掉 flushAutosave 外面的 perfSpan → 第二条红（提交前手工跑过）。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { flushAutosave, useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { emptyProject } from '@/types/document'
import { cancelProbe, finishProbe, startProbe } from './session'

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_perf_session')
}, 20000)

afterEach(() => {
  cancelProbe()
  useInteractionStore.getState().end()
})

describe('探针会话', () => {
  it('documentStore 的通知按「文档本体 / 保存状态 / 别的」分开记', async () => {
    expect(startProbe()).toBe(true)
    useInteractionStore.getState().begin('element')
    // 自动保存改保存状态：不是写文档
    useDocumentStore.setState({ saveState: 'saving' })
    useDocumentStore.setState({ saveState: 'saved' })
    // 真的改了文档本体
    useDocumentStore.getState().commit(literal('改一下'), (d) => {
      d.name = `${d.name}·`
    })
    useInteractionStore.getState().end()
    const report = (await finishProbe())!
    const seg = report.segments[0]
    expect(seg.context.doc_split).toBe(true)
    expect(seg.counts['store.document.save']).toBe(2)
    expect(seg.counts['store.document.doc']).toBeGreaterThanOrEqual(1)
    expect(seg.counts['store.document']).toBe(
      (seg.counts['store.document.save'] ?? 0) +
        (seg.counts['store.document.doc'] ?? 0) +
        (seg.counts['store.document.other'] ?? 0),
    )
  })

  it('拖动途中跑的自动保存留下 autosave.flush 计时', async () => {
    startProbe()
    useInteractionStore.getState().begin('move')
    flushAutosave()
    useInteractionStore.getState().end()
    const seg = (await finishProbe())!.segments[0]
    expect(seg.spans['autosave.flush']?.count).toBe(1)
  })
})
