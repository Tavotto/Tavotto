import { afterEach, describe, expect, it, vi } from 'vitest'
import { emptyProject } from '@/types/document'
import { ApiError, cancelProjectFile, confirmProjectFile, fetchProjectFile, putAutosave, reserveProjectFile, saveLayout } from './api'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('项目文件关联 HTTP wire', () => {
  it('仅明确404或无结构回包是旧后端；503/未来版本/坏视图不能假装无绑定', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })))
    expect(await fetchProjectFile('working', 'project')).toEqual({ status: 'unsupported' })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ code: 'session_unavailable', error: 'cannot read state' }, { status: 503 })))
    await expect(fetchProjectFile('working', 'project')).rejects.toBeInstanceOf(ApiError)
    // A catch-all 200 without any contract fields is an absent endpoint: it
    // must not block autosave (that would lose the user's edits).
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({})))
    expect(await fetchProjectFile('working', 'project')).toEqual({ status: 'unsupported' })
    // A body that claims the contract but is unreadable fails closed.
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ version: 2, status: 'ok', binding: null })))
    expect(await fetchProjectFile('working', 'project')).toEqual({ version: 1, status: 'unavailable' })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ status: 'unavailable' })))
    expect(await fetchProjectFile('working', 'project')).toEqual({ version: 1, status: 'unavailable' })
  })

  it.each(['read', 'reserve', 'confirm', 'cancel'] as const)('%s有界超时，不让saveNow永远等一个忽略abort的宿主', async operation => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
    const request = operation === 'read' ? fetchProjectFile('working', 'project')
      : operation === 'reserve' ? reserveProjectFile('working', 'project', { name: 'A', autosave_revision: null, axis: null, expected_binding_sequence: null })
      : operation === 'confirm' ? confirmProjectFile('working', 'project', { name: 'A', intent_sequence: 1, revision: 'a'.repeat(32) })
      : cancelProjectFile('working', 'project', 1)
    const waiting = expect(request).rejects.toThrow('project_file_timeout')
    await vi.advanceTimersByTimeAsync(5000)
    await waiting
    expect(vi.getTimerCount()).toBe(0)
  })

  it('旧save/AS缺省URL和raw正文不增加字段，新opts只在query传工作副本上下文', async () => {
    const requests: { url: URL; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      requests.push({ url: new URL(String(input), 'http://test'), body: init?.body ? JSON.parse(String(init.body)) : null })
      return Response.json({ ok: true, revision: 'r' })
    }))
    const document = emptyProject()
    await saveLayout('A', document)
    await putAutosave('working', document)
    expect(requests[0].url.pathname + requests[0].url.search).toBe('/api/layouts/A')
    expect(requests[1].url.pathname + requests[1].url.search).toBe('/api/autosave/working')
    expect(requests.map(request => request.body)).toEqual([document, document])
    await saveLayout('A', document, 'base', { format: 'v4', pj: 'project', target: 'project', workingDocId: 'working', bindingIntent: 7 })
    expect(requests[2].url.pathname).toBe('/api/documents/v4/layouts/A')
    expect(requests[2].url.searchParams.get('working_doc_id')).toBe('working')
    expect(requests[2].url.searchParams.get('binding_intent')).toBe('7')
    expect(requests[2].body).toEqual(document)
    const edit = { axis: { editor_id: 'this-editor', sequence: 1 }, base_axis: null, base_revision: null }
    await putAutosave('working', document, undefined, undefined, 'project', undefined, { projectFileEdit: edit })
    expect(JSON.parse(requests[3].url.searchParams.get('project_file_edit')!)).toEqual(edit)
    expect(requests[3].body).toEqual(document)
  })

  it('reserve只提交name与AS/轴/CAS，地址按格式，前端不提交file路径', async () => {
    let request: { url: URL; body: unknown } | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      request = { url: new URL(String(input), 'http://test'), body: JSON.parse(String(init?.body)) }
      return Response.json({ state: 'unconfirmed' })
    }))
    const body = { name: 'A', autosave_revision: 'a'.repeat(32), axis: null, expected_binding_sequence: 3 }
    expect(await reserveProjectFile('working', 'project', body, 'v4')).toEqual({ state: 'unconfirmed' })
    expect(request!.url.pathname).toBe('/api/documents/v4/layout-session/project-file/working/reserve')
    expect(request!.body).toEqual(body)
    expect(request!.body).not.toHaveProperty('file')
  })

  it('只有明确recovery opts增加冻结观察query，raw正文和edit各自不变', async () => {
    let url!: URL
    let sent: unknown
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      url = new URL(String(input), 'http://test'); sent = JSON.parse(String(init?.body))
      return Response.json({ ok: true })
    }))
    const document = emptyProject()
    const edit = { axis: { editor_id: 'new-branch', sequence: 1 }, base_axis: null, base_revision: 'observed-revision' }
    const proof = { revision: 'observed-revision', expected: { version: 1 as const, status: 'ok' as const, binding: null,
      intent_sequence: 0, edit: { axis: null, autosave_revision: 'old-tracked-revision', known: false } } }
    await putAutosave('working', document, undefined, 'observed-revision', 'project', 'v4', { projectFileEdit: edit, projectFileRecovery: proof })
    expect(JSON.parse(url.searchParams.get('project_file_recovery')!)).toEqual(proof)
    expect(JSON.parse(url.searchParams.get('project_file_edit')!)).toEqual(edit)
    expect(sent).toEqual(document)
  })

  it('AS正文成功但畸形/错版元信息不变成正文I/O失败，也不授予clean绑定', async () => {
    for (const project_file of [
      { state: 'accepted', version: 1, status: 'ok' },
      { state: 'accepted', version: 1, status: 'ok', binding: null, intent_sequence: 0,
        edit: { known: true, axis: null, autosave_revision: 'other-revision' } },
    ]) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ok: true, revision: 'actual-revision', project_file })))
      await expect(putAutosave('working', emptyProject())).resolves.toEqual({
        ok: true, revision: 'actual-revision', project_file: { state: 'unavailable' },
      })
    }
  })
})
