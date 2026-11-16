import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createVersion, fetchAutosave, fetchLayout, fetchLayoutList, fetchVersionDoc, probeAutosave, putAutosave, saveLayout, versionThumbUrl } from './api'
import { chartVersionProject } from './documentFormat'
import { emptyDocument, emptyProject } from '@/types/document'
import { setCurrentProjectId } from './session'

const calls: { url: string; init?: RequestInit }[] = []
let reply: unknown
let header: string | undefined
beforeEach(() => {
  calls.length = 0; reply = {}; header = 'v4'; setCurrentProjectId('p_captured')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), init })
    return Response.json(reply, { headers: { ...(header ? { 'X-Tavotto-Document-Format': header } : {}), 'X-Tavotto-Revision': 'r1' } })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })
const path = () => new URL(calls.at(-1)!.url, 'http://localhost').pathname
const body = () => JSON.parse(String(calls.at(-1)!.init?.body))

describe('schema4真实API消费者wire', () => {
  it('旧保存正文/缺省URL不增加format，v4显式到独立路径并发送实际正文', async () => {
    const pd = emptyProject()
    await saveLayout('One', pd, 'r0')
    expect(path()).toBe('/api/layouts/One'); expect(body()).toEqual(pd)
    const v4 = { ...pd, schema: 4 as const }
    await saveLayout('One', v4, 'r0', { format: 'v4', pj: 'p_captured' })
    expect(path()).toBe('/api/documents/v4/layouts/One'); expect(body()).toEqual(v4)
    await putAutosave('d_same', v4, 1, 'r0', 'p_captured', 'v4')
    expect(path()).toBe('/api/documents/v4/autosave/d_same'); expect(body()).toEqual(v4)
  })
  it('显式v4读取只接受v4 header，普通GET仍接受旧无头响应', async () => {
    reply = { layouts: ['One'] }; header = undefined
    expect(await fetchLayoutList()).toEqual({ names: ['One'], modified: {} })
    await expect(fetchLayoutList('v4')).rejects.toMatchObject({ body: { code: 'invalid_document' } })
    reply = { schema: 4 }; await expect(fetchLayout('One', 'v4')).rejects.toMatchObject({ body: { code: 'invalid_document' } })
    await expect(fetchAutosave('same', null, 'v4')).rejects.toMatchObject({ body: { code: 'invalid_document' } })
    expect((await probeAutosave('same', null, 'v4')).kind).toBe('invalid')
  })
  it('v4保存目录只消费实际字符串字段，旧返回shape完全保留', async () => {
    reply = { layouts: ['One'], document_dir: 'E:/project/tavottofile/documents/v4' }
    expect(await fetchLayoutList('v4')).toEqual({ names: ['One'], modified: {}, documentDir: 'E:/project/tavottofile/documents/v4' })
    reply = { layouts: [], document_dir: null }
    expect(await fetchLayoutList('v4')).toEqual({ names: [], modified: {} })
  })
  it('v4命名和autosave读原schema4并保留被捕获pj', async () => {
    reply = { ...emptyProject(), schema: 4 }
    const r = await fetchLayout('One', 'v4', 'p_old')
    expect(r.doc).toEqual(reply); expect(calls.at(-1)?.url).toContain('pj=p_old')
    const a = await fetchAutosave('same', 'p_old', 'v4')
    expect(a?.doc).toEqual(reply); expect(calls.at(-1)?.url).toContain('pj=p_old')
  })
  it('版本使用唯一单canvas适配器，恢复只投影该画布，不变整项目', async () => {
    const captured = emptyDocument(); captured.name = 'Active'
    const identity = { project: { id: 'project-id', name: 'Project' }, canvasId: 'canvas-active', createdAt: 1, updatedAt: 2 }
    await createVersion('d4', { doc: captured, canvasId: identity.canvasId }, 'p_old', { format: 'v4', identity })
    expect(path()).toBe('/api/documents/v4/versions/d4')
    expect(body().doc).toEqual(chartVersionProject(captured, identity))
    reply = { id: 'v1', doc: body().doc }
    const loaded = await fetchVersionDoc('d4', 'v1', 'v4')
    expect(loaded.doc.schema).toBe(2); expect(loaded.doc.name).toBe('Active')
    expect(loaded.doc).not.toHaveProperty('canvases')
  })
  it('v4版本拒绝缺头或多canvas，绝不取第一个伪装恢复成功', async () => {
    const pd = { ...emptyProject(), schema: 4 as const }; pd.canvases.push(structuredClone(pd.canvases[0]))
    reply = { doc: pd }
    await expect(fetchVersionDoc('d4', 'v1', 'v4')).rejects.toMatchObject({ body: { code: 'invalid_document' } })
    pd.canvases.pop(); header = undefined
    await expect(fetchVersionDoc('d4', 'v1', 'v4')).rejects.toMatchObject({ body: { code: 'invalid_document' } })
  })
  it('存图地址同format，旧地址逐字不变', () => {
    const meta = { id: 'v1', thumb: 'png' as const }
    expect(versionThumbUrl('d', meta)).toContain('/api/versions/d/v1/thumb?f=png')
    expect(versionThumbUrl('d', meta, 'v4')).toContain('/api/documents/v4/versions/d/v1/thumb?f=png')
  })
})
