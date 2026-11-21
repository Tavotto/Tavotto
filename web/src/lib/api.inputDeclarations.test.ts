import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  declareInputs,
  fetchDeclaredInputs,
  fetchInputRemap,
  fetchPreparationInputDeclarationContext,
  fetchPreparationSession,
  removeDeclaredInputs,
  resolveDeclaredInputProjectFile,
} from './api'
import { setCurrentProjectId } from './session'

afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

const state = { version: 1, revision: null, declarations: [] }
const scope = { script: 'tools/中文 plot.py', cwd_mode: 'project_root' as const, run_config: null }

describe('输入声明沿原 API 与明确项目归属', () => {
  it('默认准备轮询不扩响应请求；编辑器显式读取冻结 scope，不创建会话', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}')))
    vi.stubGlobal('fetch', fetcher)
    const abort = new AbortController()
    await fetchPreparationSession('session /甲', 'project-a', abort.signal)
    await fetchPreparationInputDeclarationContext('session /甲', 'project-a', abort.signal)
    const [ordinary, editor] = fetcher.mock.calls
    expect(ordinary[0]).not.toContain('include_input_declaration')
    expect(editor[0]).toContain('session%20%2F%E7%94%B2?include_input_declaration=1')
    for (const [, options] of fetcher.mock.calls) {
      expect(options.method ?? 'GET').toBe('GET')
      expect(options.headers['X-Tavotto-Project']).toBe('project-a')
      expect(options.cache).toBe('no-store')
      expect(options.signal).toBe(abort.signal)
      expect(options.body).toBeUndefined()
    }
  })

  it('选择解析和批准表只读，路径保留编码且请求不跟随已切换项目', async () => {
    const path = 'data/中文 & values.csv'
    const remap = { rules: [{ kind: 'prefix', from: '', to: 'D:/data' }], generation: 8 }
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ input_declarations: state })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ project_file: path })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ input_remap: remap })))
    vi.stubGlobal('fetch', fetcher)
    setCurrentProjectId('project-b')
    expect(await fetchDeclaredInputs('project-a')).toEqual(state)
    expect(await resolveDeclaredInputProjectFile(path, 'project-a')).toBe(path)
    expect(await fetchInputRemap('project-a')).toEqual(remap)
    expect(fetcher.mock.calls[0][0]).toContain('include_selection_revisions=1')
    expect(fetcher.mock.calls[1][0]).toContain(`project_file=${encodeURIComponent(path)}`)
    for (const [, options] of fetcher.mock.calls) {
      expect(options.headers['X-Tavotto-Project']).toBe('project-a')
      expect(options.body).toBeUndefined()
    }
  })

  it('写设置只发精确 scope、整库 CAS 和所见批准表代次，空 from 保留', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ input_declarations: state }))))
    vi.stubGlobal('fetch', fetcher)
    const body = { ...scope, files: ['data.csv'], remaps: [''], expected_revision: null, expected_remap_generation: 8 }
    expect(await declareInputs(body, 'project-a')).toEqual(state)
    expect(await removeDeclaredInputs({ ...scope, expected_revision: 'a'.repeat(32) }, 'project-a')).toEqual(state)
    expect(fetcher.mock.calls.map(([, options]) => options.method)).toEqual(['POST', 'DELETE'])
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body)
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...scope, expected_revision: 'a'.repeat(32) })
  })

  it.each([409, 503])('失败保留后端 %s 身份，不替换为空声明', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'input_declaration_conflict' }), { status })))
    await expect(fetchDeclaredInputs('project-a')).rejects.toMatchObject({ status, body: { code: 'input_declaration_conflict' } } as Partial<ApiError>)
  })
})
