import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject } from '@/types/document'
import { __setPresenceChannelFactory } from '@/lib/docPresence'
import { forgetProjectFilesInSession, writeProjectFile, type ProjectFileBinding } from '@/lib/projectFile'
import { openLayoutDocument } from './actions'
import { setProjectFile, useDocumentStore } from './documentStore'

/**
 * QA-WIN-46：版本时间线按 documentId 分存。从「项目里的排版」重开同一个文件必须沿用上次的
 * documentId，否则命名节点（挂在旧 id 下）在新会话里看不到。
 */

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()
const bind = (file: string, over: Partial<ProjectFileBinding> = {}): ProjectFileBinding => ({
  projectId: 'pj1', name: file.replace(/^.*\//, '').replace(/\.json$/, ''), file, revision: 'r1', dirty: false, ...over,
})

describe('重开项目里的同一个排版文件', () => {
  beforeEach(async () => {
    localStorage.clear()
    forgetProjectFilesInSession()
    await s().switchDocument(emptyProject(), 'd_scratch')
  })

  it('沿用上次的 documentId（时间线接得上）', async () => {
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    const first = s().documentId
    // 模拟重启：换到别的排版，再从项目文件重开
    await s().switchDocument(emptyProject(), 'd_other')
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json', { revision: 'r2' }))
    expect(s().documentId).toBe(first)
    expect(s().projectFile?.revision).toBe('r2')
  })

  it('不同文件各有各的身份，不共用时间线', async () => {
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    const a = s().documentId
    await openLayoutDocument(emptyProject(), bind('tavottofile/b.json'))
    expect(s().documentId).not.toBe(a)
  })

  it('另存为：当前排版改绑新文件后，重开原文件得到新身份，不继承历史', async () => {
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    const a = s().documentId
    setProjectFile(bind('tavottofile/copy.json')) // 另存为 = 同一份排版改绑新文件
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    expect(s().documentId).not.toBe(a)
  })

  it('旧绑定有未保存修改（dirty）：不盖它的本机槽位，给新身份', async () => {
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    const a = s().documentId
    setProjectFile(bind('tavottofile/a.json', { dirty: true }))
    await s().switchDocument(emptyProject(), 'd_other')
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    expect(s().documentId).not.toBe(a)
  })

  it('不是项目文件的来源（项目包等）仍是全新身份', async () => {
    await openLayoutDocument(emptyProject())
    const x = s().documentId
    await openLayoutDocument(emptyProject())
    expect(s().documentId).not.toBe(x)
  })

  it('多份候选：选最近绑定的那份，不按 id 排', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(1000)
      writeProjectFile('d_zzz', bind('tavottofile/a.json'))
      vi.setSystemTime(2000)
      writeProjectFile('d_aaa', bind('tavottofile/a.json'))
      vi.setSystemTime(3000)
      writeProjectFile('d_mmm', bind('tavottofile/a.json')) // 最近
    } finally {
      vi.useRealTimers()
    }
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    expect(s().documentId).toBe('d_mmm')
  })

  describe('旧身份正在别的窗口开着', () => {
    afterEach(() => __setPresenceChannelFactory(null))

    /** 别的标签页端着 held 里的文档：收到探测就回音 */
    const otherTabHolds = (held: string[]) =>
      __setPresenceChannelFactory(() => {
        let listener: ((ev: { data: unknown }) => void) | null = null
        return {
          addEventListener: (_t, l) => { listener = l },
          close: () => {},
          postMessage: (d) => {
            const m = d as { type: string; docId: string; reqId: string }
            if (m.type === 'doc-probe' && held.includes(m.docId)) {
              queueMicrotask(() => listener?.({ data: { type: 'doc-probe-ack', docId: m.docId, tabId: 'other', reqId: m.reqId } }))
            }
          },
        }
      })

    it('被占用的不沿用，退到下一份没人占的；都占用就新建（打开两次是两个会话）', async () => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(1000)
      writeProjectFile('d_old', bind('tavottofile/a.json'))
      vi.setSystemTime(2000)
      writeProjectFile('d_new', bind('tavottofile/a.json'))
      vi.useRealTimers()
      otherTabHolds(['d_new'])
      await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
      expect(s().documentId).toBe('d_old')
      await s().switchDocument(emptyProject(), 'd_scratch')
      otherTabHolds(['d_new', 'd_old'])
      await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
      expect(['d_new', 'd_old']).not.toContain(s().documentId)
    })
  })

  it('别的窗口后来把这份置 dirty（本窗口会话缓存仍是干净旧绑定）：不沿用，不盖它的槽位', async () => {
    writeProjectFile('d_old', bind('tavottofile/a.json')) // 本窗口会话缓存：干净
    // 窗口 B 之后在同一份上编辑未存项目文件：只改了持久副本
    const raw = JSON.parse(localStorage.getItem('tavotto.projectFile.d_old')!)
    localStorage.setItem('tavotto.projectFile.d_old', JSON.stringify({ ...raw, dirty: true }))
    await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
    expect(s().documentId).not.toBe('d_old')
  })

  describe('探测占用期间项目或排版变了', () => {
    afterEach(() => __setPresenceChannelFactory(null))

    it('不把旧项目的排版装进新选的工作区，返回 false', async () => {
      writeProjectFile('d_old', bind('tavottofile/a.json'))
      __setPresenceChannelFactory(() => {
        let listener: ((ev: { data: unknown }) => void) | null = null
        return {
          addEventListener: (_t, l) => { listener = l },
          close: () => {},
          postMessage: (d) => {
            const m = d as { type: string; docId: string; reqId: string }
            if (m.type !== 'doc-probe') return
            // 探测还在等回音时，用户切到了另一份排版（loadSeq 变）
            void s().switchDocument(emptyProject(), 'd_switched').then(() =>
              listener?.({ data: { type: 'doc-probe-ack', docId: 'd_nobody', tabId: 'other', reqId: m.reqId } }),
            )
          },
        }
      })
      const ok = await openLayoutDocument(emptyProject(), bind('tavottofile/a.json'))
      expect(ok).toBe(false)
      expect(s().documentId).toBe('d_switched')
    })
  })
})
