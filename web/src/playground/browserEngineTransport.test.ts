/**
 * BrowserEngineTransport：与 MCP 传输同一地位的第三条传输。
 * 契约照抄 renderStore 消费端的期望——rev/manifest/svg/warnings 同一响应，
 * 失败转成带 code 的 EngineError，panelSrc 回 null（退回 SVG 显示）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EngineError, type Manifest } from '@/lib/api'
import { engineTransport, setEngineTransport } from '@/lib/engineTransport'
import { installBrowserTransport } from './browserEngineTransport'
import { PlaygroundClient, PlaygroundError } from './pyodideClient'

const manifest = { stem: 'F', size_mm: [80, 60], elements: [] } as unknown as Manifest

function fakeClient(overrides: Partial<Record<'render' | 'previewPng', unknown>> = {}) {
  return {
    render: vi.fn(async () => ({
      manifest,
      svg: '<svg/>',
      warnings: [],
      patch_hash: 'sha256:x',
      render_revision: 7,
    })),
    previewPng: vi.fn(async () => 'QkFTRTY0'),
    ...overrides,
  } as unknown as PlaygroundClient
}

afterEach(() => setEngineTransport(null))

describe('installBrowserTransport', () => {
  it('render：fileId 去掉 .pdf 得 stem，响应映射成 EngineRenderResponse', async () => {
    const client = fakeClient()
    installBrowserTransport(client)
    const res = await engineTransport()!.render('MyFig.pdf', [{ gid: 'g', prop: 'p', value: 1 }], {
      previewDpi: 100,
    })
    expect(client.render).toHaveBeenCalledWith(
      'MyFig',
      [{ gid: 'g', prop: 'p', value: 1 }],
      100,
      undefined,
    )
    expect(res.rev).toBe(7)
    expect(res.manifest).toBe(manifest)
    expect(res.svg).toBe('<svg/>')
  })

  it('失败转成 EngineError（code 保留，渲染态据此分诊）', async () => {
    installBrowserTransport(
      fakeClient({
        render: vi.fn(async () => {
          throw new PlaygroundError({ code: 'render_error', message: '炸了', traceback: 'tb' })
        }),
      }),
    )
    const err = await engineTransport()!
      .render('F.pdf', [])
      .catch((e) => e)
    expect(err).toBeInstanceOf(EngineError)
    expect((err as EngineError).code).toBe('render_error')
    expect((err as EngineError).traceback).toBe('tb')
  })

  it('previewPngUrl 回 data URL；panelSrc 回 null（显示退回 SVG）', async () => {
    installBrowserTransport(fakeClient())
    const t = engineTransport()!
    expect(await t.previewPngUrl('F.pdf', [], 800)).toBe('data:image/png;base64,QkFTRTY0')
    expect(t.panelSrc('F.pdf', 'pdf', 800)).toBeNull()
  })

  it('卸载后回到默认传输', () => {
    const undo = installBrowserTransport(fakeClient())
    expect(engineTransport()).not.toBeNull()
    undo()
    expect(engineTransport()).toBeNull()
  })
})
