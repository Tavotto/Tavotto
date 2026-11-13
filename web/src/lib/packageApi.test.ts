import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPackage } from './api'
import { setCurrentProjectId } from './session'
import { emptyDocument, emptyProject } from '@/types/document'

const result = { name: 'Project.tavotto', url: '/api/download/Project.tavotto', assets: 2, missing: [] }
const calls: { url: URL; init?: RequestInit }[] = []
let reply: unknown
beforeEach(() => {
  calls.length = 0
  reply = result
  setCurrentProjectId('p_current')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    calls.push({ url: new URL(String(url), 'http://localhost'), init })
    return Response.json(reply)
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

describe('项目包导出格式与项目地址', () => {
  it.each([emptyDocument(), emptyProject()])('旧三参保留schema $schema正文、设置和默认URL/项目头', async doc => {
    const settings = { dpi: 300 }
    expect(await createPackage('Old', doc, settings)).toEqual(result)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/package')
    expect(calls[0].url.searchParams.get('pj')).toBe('p_current')
    expect(new Headers(calls[0].init?.headers).get('X-Tavotto-Project')).toBe('p_current')
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ stem: 'Old', doc, settings })
  })

  it('v4显式路由与捕获pj不受当前项目改变影响，保原四键与来源数量', async () => {
    const doc = { ...emptyProject(), schema: 4 as const }
    reply = { ...result, document_format: 'v4', chart_sources: 3 }
    expect(await createPackage('New', doc, {}, { format: 'v4', pj: 'p_captured' })).toEqual(reply)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package')
    expect(calls[0].url.searchParams.get('pj')).toBe('p_captured')
    expect(new Headers(calls[0].init?.headers).get('X-Tavotto-Project')).toBe('p_captured')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ stem: 'New', doc, settings: {} })
  })

  it('显式null项目不借当前项目，显式legacy仍走原路由', async () => {
    await createPackage('Old', emptyProject(), {}, { format: 'legacy', pj: null })
    expect(calls[0].url.pathname).toBe('/api/package')
    expect(calls[0].url.searchParams.has('pj')).toBe(false)
    expect(new Headers(calls[0].init?.headers).has('X-Tavotto-Project')).toBe(false)
  })

  it.each([undefined, 'legacy', 'v5', null])('v4回执格式%s不伪装导出成功，也不尝试旧路由', async format => {
    reply = { ...result, ...(format === undefined ? {} : { document_format: format }) }
    await expect(createPackage('New', { ...emptyProject(), schema: 4 }, {}, { format: 'v4', pj: 'p_captured' }))
      .rejects.toMatchObject({ body: { code: 'invalid_document' } })
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package')
  })

  it('v4真实API拒绝沿既有ApiError传出，不降旧包保存', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ code: 'chart_source_missing', error: 'Source unavailable' }, { status: 422 })))
    await expect(createPackage('New', { ...emptyProject(), schema: 4 }, {}, { format: 'v4', pj: 'p_captured' }))
      .rejects.toMatchObject({ status: 422, body: { code: 'chart_source_missing' } })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
