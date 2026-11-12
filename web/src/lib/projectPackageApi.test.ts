import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyDocument, emptyProject } from '@/types/document'
import {
  ApiError, createPackage, inspectProjectPackage, installProjectPackage, openPackage,
  type InputRemapRule, type ProjectPackageInspection, type ProjectPackageInstallation,
} from './api'
import { currentProjectId, setCurrentProjectId } from './session'

const calls: { url: URL; init?: RequestInit }[] = []
let reply: unknown
let status: number
let responseHeaders: HeadersInit

function documentV4() {
  const doc = { ...emptyProject(), schema: 4 as const }
  doc.canvases.push({ ...structuredClone(doc.canvases[0]), id: 'second-canvas', name: '第二张画布' })
  doc.activeCanvasId = 'second-canvas'
  return doc
}

function inspection(): ProjectPackageInspection {
  return {
    document_format: 'v4', doc: documentV4(),
    manifest: { version: 4, execution: { version: 2, captures: [] } },
    resources: [{ target: '数据/input.csv', record: { size: 7, sha256: 'a'.repeat(64) } }],
    missing: ['unavailable.csv'], warnings: [{ reason: 'captured_input_channels_unverified', id: 'capture-a' }],
    figure_snapshots: 2,
    execution_coverage: { state: 'incomplete', issues: [{ script: 'plot.py', reason: 'inputs_unknown' }] },
    captured_inputs: [{ revision: 'capture-revision', scope: { script: 'plot.py' } }],
  }
}

function installation(): ProjectPackageInstallation {
  return {
    state: 'resources_installed', path: 'E:/新项目/计算图表', document_format: 'v4', doc: documentV4(),
    references: [{ old: { patchHash: 'old-patch' }, new: { patchHash: 'same-patch', sourceId: 'plot.py' } }],
    execution: { version: 2, contexts: [], captures: [] }, remap_fingerprint: 'f'.repeat(64),
    missing: ['missing.csv'], warnings: [{ reason: 'captured_input_channels_unverified', id: 'capture-a' }],
    execution_coverage: { state: 'incomplete', issues: [{ script: 'plot.py', reason: 'inputs_unknown' }] },
    configuration: 'ready',
    configuration_review: { version: 1, existing_local_metadata: false, workdir: null, defaults: [], issues: [] },
    document_saved: false,
  }
}

function upload(): File {
  return new File(['opaque-package-bytes'], '研究项目.tavotto', { type: 'application/zip' })
}

function requestForm(index = 0): FormData {
  const request = calls[index]
  expect(request.init?.method).toBe('POST')
  expect(new Headers(request.init?.headers).has('Content-Type')).toBe(false)
  expect(request.init?.body).toBeInstanceOf(FormData)
  return request.init!.body as FormData
}

function expectProject(index: number, pj: string | null) {
  expect(calls[index].url.searchParams.get('pj')).toBe(pj)
  expect(new Headers(calls[index].init?.headers).get('X-Tavotto-Project')).toBe(pj)
}

async function apiFailure(request: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await request.then(() => null, reason => reason)
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

beforeEach(() => {
  calls.length = 0
  reply = inspection()
  status = 200
  responseHeaders = {}
  setCurrentProjectId('p_current')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    calls.push({ url: new URL(String(url), 'http://localhost'), init })
    return Response.json(reply, { status, headers: responseHeaders })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  setCurrentProjectId(null)
})

describe('v4项目包检视和安装的HTTP边界', () => {
  it.each(['p_captured', null])('检视固定请求所属项目%s，单文件只发显式检视，不打开或安装', async pj => {
    const file = upload()
    const expected = reply
    const pending = inspectProjectPackage(file, pj)
    setCurrentProjectId('p_later')
    expect(await pending).toEqual(expected)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/inspect')
    expectProject(0, pj)
    const form = requestForm()
    expect([...form.keys()]).toEqual(['package'])
    expect(form.getAll('package')).toEqual([file])
    expect(currentProjectId()).toBe('p_later')
  })

  it.each(['p_captured', null])('安装固定请求所属项目%s，缺省改指不增加options字段', async pj => {
    reply = installation()
    status = 201
    const file = upload()
    const options = { destination: 'E:/目标 目录/研究' }
    const pending = installProjectPackage(file, options, pj)
    setCurrentProjectId('p_later')
    expect(await pending).toEqual(reply)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/install')
    expectProject(0, pj)
    const form = requestForm()
    expect([...form.keys()]).toEqual(['package', 'options'])
    expect(form.getAll('package')).toEqual([file])
    expect(form.getAll('options')).toHaveLength(1)
    expect(JSON.parse(String(form.get('options')))).toEqual(options)
    expect(currentProjectId()).toBe('p_later')
  })

  it('安装仅发送批准规则kind/from/to，保留根prefix与原顺序，不泄漏查询事实', async () => {
    reply = installation()
    const rules: InputRemapRule[] = [
      { kind: 'prefix', from: '', to: 'E:/数据集', added_at: 123, target_exists: true },
      { kind: 'file', from: 'D:/旧数据/input.csv', to: 'E:/数据集/新 input.csv', added_at: null, target_exists: false },
    ]
    const original = structuredClone(rules)
    await installProjectPackage(upload(), { destination: 'E:/目标', remap_rules: rules }, 'p_captured')
    expect(JSON.parse(String(requestForm().get('options')))).toEqual({
      destination: 'E:/目标', remap_rules: [
        { kind: 'prefix', from: '', to: 'E:/数据集' },
        { kind: 'file', from: 'D:/旧数据/input.csv', to: 'E:/数据集/新 input.csv' },
      ],
    })
    expect(rules).toEqual(original)
    expect(calls).toHaveLength(1)
  })

  it('明确空改指保留空数组，不变成省略的包默认规则', async () => {
    reply = installation()
    await installProjectPackage(upload(), { destination: 'E:/目标', remap_rules: [] }, null)
    expect(JSON.parse(String(requestForm().get('options')))).toEqual({ destination: 'E:/目标', remap_rules: [] })
    expect(calls).toHaveLength(1)
  })

  it.each([
    { state: 'resources_installed', configuration: 'pending', configuration_error: 'run_config_conflict' },
    { state: 'resources_installed', configuration: 'needs_review' },
    { state: 'publication_unconfirmed', configuration: 'pending' },
    { state: 'installation_unconfirmed', configuration: 'ready', upload_cleanup: 'pending' },
  ] as const)('503保完整已发布回执$state/$configuration，不重发不把正文算作已保存', async phase => {
    const expected = {
      ...installation(), ...phase,
      configuration_review: {
        version: 1, existing_local_metadata: true,
        workdir: { mode: 'project_root', modes: ['sandbox', 'script_dir', 'project_root'], decided: true },
        defaults: [{ script: 'plot.py', selection: { id: 'rc_local', label: '本机参数' }, unavailable_code: null }],
        issues: [{ reason: 'existing_local_metadata', script: null }],
      },
    }
    reply = expected
    status = 503
    const actual = await installProjectPackage(upload(), { destination: expected.path }, 'p_captured')
    expect(actual).toEqual(expected)
    expect(actual.document_saved).toBe(false)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/install')
    expect(currentProjectId()).toBe('p_current')
  })

  it.each([401, 409])('%s即使夹带回执也保持ApiError和原错误体，不当作已发布成功', async errorStatus => {
    reply = { ...installation(), code: errorStatus === 401 ? 'unauthorized' : 'package_destination_exists', error: '拒绝安装' }
    status = errorStatus
    responseHeaders = { 'X-Tavotto-Diagnostic-Ref': 'diagnostic-ref' }
    const error = await apiFailure(installProjectPackage(upload(), { destination: 'E:/目标' }, 'p_captured'))
    expect(error.status).toBe(errorStatus)
    expect(error.body).toEqual(reply)
    expect(error.diagnosticRef).toBe('diagnostic-ref')
    expect(calls).toHaveLength(1)
  })

  it.each([
    ['无回执', {}],
    ['空回执', null],
    ['旧格式', { document_format: 'legacy' }],
    ['旧正文', { doc: emptyProject() }],
    ['无路径', { path: '' }],
    ['未知发布阶段', { state: 'ready' }],
    ['未知配置阶段', { configuration: 'complete' }],
    ['谎报正文已存', { document_saved: true }],
    ['缺件不是数组', { missing: null }],
    ['警告不是数组', { warnings: null }],
  ])('503%s沿ApiError保原body，不退旧路径或自动重发', async (name, patch) => {
    reply = name === '无回执' ? { code: 'installation_unavailable', error: '未发布' }
      : patch === null ? null : { ...installation(), ...patch }
    status = 503
    const error = await apiFailure(installProjectPackage(upload(), { destination: 'E:/目标' }, null))
    expect(error.status).toBe(503)
    expect(error.body).toEqual(reply)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/install')
  })

  it.each([null, {}, { document_format: 'v4', doc: { schema: 3 } }])('2xx坏安装回执%j抛ApiError并保原回执', async malformed => {
    reply = malformed
    const error = await apiFailure(installProjectPackage(upload(), { destination: 'E:/目标' }, 'p_captured'))
    expect(error.status).toBe(400)
    expect(error.body).toEqual({ code: 'invalid_document', receipt: malformed })
    expect(calls).toHaveLength(1)
  })

  it.each([null, {}, { document_format: 'v4', doc: { schema: 3 } }])('2xx坏检视回执%j抛ApiError且不降旧包接口', async malformed => {
    reply = malformed
    const error = await apiFailure(inspectProjectPackage(upload(), 'p_captured'))
    expect(error.status).toBe(400)
    expect(error.body.code).toBe('invalid_document')
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/inspect')
  })

  it.each([401, 409, 422, 503])('检视%s保原ApiError事实，只发一次检视', async errorStatus => {
    reply = { code: 'package_invalid', error: '包核验失败', params: { member: 'layout.json' } }
    status = errorStatus
    const error = await apiFailure(inspectProjectPackage(upload(), 'p_captured'))
    expect(error.status).toBe(errorStatus)
    expect(error.body).toEqual(reply)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/documents/v4/package/inspect')
  })

  it('网络失败不自动重发可能已发布的安装', async () => {
    const failure = new TypeError('Failed to fetch')
    vi.stubGlobal('fetch', vi.fn(async () => { throw failure }))
    await expect(installProjectPackage(upload(), { destination: 'E:/目标' }, null)).rejects.toBe(failure)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

describe('普通项目包默认wire兼容', () => {
  it('旧openPackage仍用当前项目与单package表单，不增v4格式或options', async () => {
    reply = { doc: emptyProject(), manifest: { scripts: ['plot.py'] }, missing: ['missing.pdf'], drifted: ['old.svg'] }
    const file = upload()
    expect(await openPackage(file)).toEqual(reply)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/package/open')
    expectProject(0, 'p_current')
    const form = requestForm()
    expect([...form.keys()]).toEqual(['package'])
    expect(form.getAll('package')).toEqual([file])
  })

  it.each([emptyDocument(), emptyProject()])('旧createPackage三参保schema$schema、JSON正文与缺省路径', async doc => {
    reply = { name: '研究.tavotto', url: '/api/download/研究.tavotto', assets: 1, missing: [] }
    const settings = { dpi: 300 }
    expect(await createPackage('研究', doc, settings)).toEqual(reply)
    expect(calls).toHaveLength(1)
    expect(calls[0].url.pathname).toBe('/api/package')
    expectProject(0, 'p_current')
    expect(calls[0].init?.method).toBe('POST')
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ stem: '研究', doc, settings })
  })
})
