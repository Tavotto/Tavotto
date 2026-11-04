import { beforeEach, describe, expect, it } from 'vitest'
import { emptyProject } from '@/types/document'
import { forgetProjectFilesInSession, type ProjectFileBinding } from '@/lib/projectFile'
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
})
