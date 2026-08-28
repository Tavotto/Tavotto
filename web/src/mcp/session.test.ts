/**
 * Codex 内嵌画布的会话层。
 *
 * 三条纪律各一组用例：
 *
 * 1. **不建第二套画布状态**：一切都灌进既有 stores，拖拽/命中/undo 用的
 *    还是同一份代码；
 * 2. **真相在服务端**：session_id / manifest / SVG / patch hash 全部来自工具
 *    响应，iframe 的 localStorage 与 widgetState 里**不存业务数据**；
 * 3. **失败不静默**：被拒的 patch、渲染错误都要能透出去。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { EngineError } from '@/lib/api'
import { engineTransport, setEngineTransport } from '@/lib/engineTransport'
import { setOverride } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import type { AppsBridge, ToolCallResult } from './appsBridge'
import { installMcpTransport, seedSession, unwrap, type OpenFigureResult } from './session'

const manifest = (tickPt = 9): Manifest =>
  ({
    stem: 'FigM',
    size_mm: [80, 60],
    elements: [
      {
        gid: 'axes_0.xticks',
        role: 'ticks',
        label: 'x 刻度',
        bbox: [0.1, 0.9, 0.8, 0.05],
        draggable: false,
        editable: [{ prop: 'fontsize', type: 'number', value: tickPt }],
      },
    ],
  }) as unknown as Manifest

const openResult = (): OpenFigureResult => ({
  ok: true,
  session_id: 's-abc',
  project: '/tmp/figures',
  stem: 'FigM',
  script: 'figm.py',
  manifest: manifest(),
  svg: '<svg width="1pt" height="1pt"><g/></svg>',
  patch_hash: 'sha256:0',
  render_revision: 1,
  profile: { profile_id: 'lab-publication-v1', profile_version: '1.0.0' },
})

function fakeBridge(handler: (name: string, args: Record<string, unknown>) => ToolCallResult) {
  const calls: { name: string; args: Record<string, unknown> }[] = []
  const bridge = {
    calls,
    // 形参与真 AppsBridge.callTool 对齐（含 timeoutMs / signal）——假件比真件
    // 少一个参数的话，「signal 有没有转下去」这类断言根本写不出来
    callTool: vi.fn(async (
      name: string,
      args: Record<string, unknown>,
      _timeoutMs?: number,
      _signal?: AbortSignal,
    ) => {
      calls.push({ name, args })
      return handler(name, args)
    }),
  }
  return bridge as unknown as AppsBridge & typeof bridge
}

const okResult = (body: Record<string, unknown>): ToolCallResult => ({
  content: [{ type: 'text', text: 'ok' }],
  structuredContent: { ok: true, ...body },
})

let restore = () => {}

afterEach(() => {
  restore()
  restore = () => {}
  setEngineTransport(null)
})

beforeEach(() => {
  restore()
  restore = () => {}
  useRenderStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
  localStorage.clear()
})

describe('seedSession', () => {
  it('把工具响应灌进既有 stores，而不是另建一套画布状态', () => {
    const open = openResult()
    const { panelId, fileId } = seedSession(open)

    const doc = useDocumentStore.getState().doc
    expect(doc.objects).toHaveLength(1)
    const panel = doc.objects[0] as PanelObject
    expect(panel.id).toBe(panelId)
    expect(panel.fileId).toBe(fileId)
    // 页面 = 这张图自己的尺寸；面板原生尺寸来自 manifest 的 size_mm
    expect([doc.page.w, doc.page.h]).toEqual([80, 60])
    expect([panel.nativeW, panel.nativeH]).toEqual([80, 60])
    expect(panel.overrides).toEqual([])

    // 素材表也是同一份（预检、属性页都从它取）
    expect(useAssetStore.getState().byId[fileId].script).toBe('figm.py')

    // 渲染态：manifest 与 SVG 来自同一次响应，且已经是 ready
    const entry = useRenderStore.getState().byKey[renderKey(fileId, [])]
    expect(entry.status).toBe('ready')
    expect(entry.manifest).toBe(open.manifest)
    expect(entry.svg).toContain('preserveAspectRatio="none"')
    expect(entry.lastPatches).toBe('[]')
    // 文件级跟踪位：显示必须走引擎产物（iframe 里没有 /api/render）
    expect(useRenderStore.getState().tracked[fileId]).toBe(true)
    // 直接进图内编辑态
    expect(useUiStore.getState().elementPanelId).toBe(panelId)
  })

  it('打开动作不进撤销栈——第一次撤销要回到「刚打开的样子」', () => {
    seedSession(openResult())
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useDocumentStore.getState().canUndo()).toBe(false)
  })

  it('不往 localStorage 写任何业务数据（iframe 随时会被重建）', () => {
    seedSession(openResult())
    const dump = JSON.stringify(Object.entries(localStorage))
    expect(dump).not.toContain('s-abc')
    expect(dump).not.toContain('FigM')
  })
})

describe('MCP 传输', () => {
  it('拖动 → setOverride → 走 tools/call 发全量 patches，manifest 用响应更新', async () => {
    const open = openResult()
    const { panelId, fileId } = seedSession(open)
    const next = manifest(7)
    const bridge = fakeBridge(() =>
      okResult({
        manifest: next,
        svg: '<svg width="1pt"><g id="new"/></svg>',
        patch_hash: 'sha256:1',
        render_revision: 2,
        warnings: [],
        rejected: [],
        applied: 1,
      }),
    )
    restore = installMcpTransport(bridge)

    // 用户在画布里改了一个值（属性页 / 拖拽最终都走这条）。
    // **不自己再调一次 render**：`immediate` 已经发出去了，重复调只会排队，
    // 然后在「还在渲染」上断言失败——那是用例的错，不是实现的错。
    setOverride(panelId, 'axes_0.xticks', 'fontsize', 7, 'immediate')
    const patches = (useDocumentStore.getState().doc.objects[0] as PanelObject).overrides
    await vi.waitFor(() =>
      expect(useRenderStore.getState().byKey[renderKey(fileId, patches)]?.status).toBe('ready'),
    )

    expect(bridge.calls).toHaveLength(1)
    expect(bridge.calls[0].name).toBe('tavotto_apply_overrides')
    expect(bridge.calls[0].args.session_id).toBe('s-abc')
    // **全量列表语义**：发的是完整的一份，不是增量
    expect(bridge.calls[0].args.patches).toEqual([
      { gid: 'axes_0.xticks', prop: 'fontsize', value: 7 },
    ])

    const entry = useRenderStore.getState().byKey[renderKey(fileId, patches)]
    expect(entry.status).toBe('ready')
    expect(entry.manifest).toBe(next)
    expect(entry.svg).toContain('id="new"')
    expect(entry.rev).toBe(2)
  })

  it('render 把 AbortSignal 转给 callTool —— 否则看门狗对内嵌画布形同虚设', async () => {
    // renderStore 给每次渲染挂了按脚本 cost 分级的看门狗（2/5/15 分钟），
    // 超时调 ctrl.abort()。HTTP 那条路把 signal 交给 fetch；这条路要是把它
    // 丢掉，卡死的渲染既不会被取消也不会报错，画布一直转。
    const { fileId } = seedSession(openResult())
    const bridge = fakeBridge(() =>
      okResult({ manifest: manifest(), svg: '<svg/>', render_revision: 2 }),
    )
    restore = installMcpTransport(bridge)

    const ctrl = new AbortController()
    await engineTransport()!.render(fileId, [], { signal: ctrl.signal })
    expect(bridge.callTool.mock.calls[0][3]).toBe(ctrl.signal)
  })

  it('工具报错 → 渲染态转 error 并带上机器可读 code，不静默', async () => {
    const { fileId } = seedSession(openResult())
    const bridge = fakeBridge(() => ({
      isError: true,
      content: [{ type: 'text', text: '[worker_timeout] 渲染超时' }],
      structuredContent: { ok: false, code: 'worker_timeout', error: '渲染超时' },
    }))
    restore = installMcpTransport(bridge)

    const patches = [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 7 }]
    await useRenderStore.getState().render(fileId, patches)
    const entry = useRenderStore.getState().byKey[renderKey(fileId, patches)]
    expect(entry.status).toBe('error')
    expect(entry.code).toBe('worker_timeout')
    // error 是**描述符**（切语言后要跟着换），显示那一刻才成文
    expect(formatMessage(entry.error)).toContain('渲染超时')
  })

  it('iframe 里没有可寻址的 HTTP 资源：panelSrc 回 null，显示退回 SVG', () => {
    restore = installMcpTransport(fakeBridge(() => okResult({})))
    expect(engineTransport()?.panelSrc('FigM.pdf', 'pdf', 800)).toBeNull()
  })

  it('卸载后回到默认（没有覆盖）那条，不影响同一个进程里的别人', () => {
    expect(engineTransport()).toBeNull()
    const undo = installMcpTransport(fakeBridge(() => okResult({})))
    expect(engineTransport()).not.toBeNull()
    undo()
    expect(engineTransport()).toBeNull()
  })
})

describe('raster 档：内嵌画布不能变成空白（ADR 0021）', () => {
  const RASTER = {
    mode: 'raster' as const,
    reason: 'svg_hard_limit' as const,
    svg_bytes: 126_132_735,
    rasterized_artist_count: 0,
  }
  const PNG = 'iVBORw0KGgo='

  it('open 就是 raster：第一帧的位图取自同一次响应', async () => {
    const open: OpenFigureResult = {
      ...openResult(),
      svg: null,
      preview: RASTER,
      preview_png_base64: PNG,
    }
    const { fileId } = seedSession(open)
    restore = installMcpTransport(fakeBridge(() => okResult({})))

    // 种进 store 的表示法就是引擎给的那一档——画布据此走位图
    expect(useRenderStore.getState().get(renderKey(fileId, [])).preview.mode).toBe('raster')
    await expect(engineTransport()!.previewPngUrl(fileId, [], 800)).resolves.toBe(
      `data:image/png;base64,${PNG}`,
    )
  })

  it('位图按变体配对：拿不到这一版自己的就宁可没有', async () => {
    const { fileId } = seedSession({ ...openResult(), svg: null, preview: RASTER,
      preview_png_base64: PNG })
    restore = installMcpTransport(fakeBridge(() => okResult({})))

    // 另一组 patches 的位图还没回来——绝不把 `[]` 那张喂给它
    // （HTTP 那条路上正是为此才不再用「谁最后渲染谁说了算」的 /api/engine/png）
    await expect(
      engineTransport()!.previewPngUrl(fileId, [{ gid: 'g', prop: 'fontsize', value: 11 }], 800),
    ).rejects.toThrowError(EngineError)
  })

  it('apply 之后位图跟着这一版走', async () => {
    const { fileId } = seedSession({ ...openResult(), svg: null, preview: RASTER })
    const patches = [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 11 }]
    restore = installMcpTransport(
      fakeBridge(() =>
        okResult({
          manifest: manifest(11),
          svg: null,
          preview: RASTER,
          preview_png_base64: PNG,
          render_revision: 2,
        }),
      ),
    )

    const res = await engineTransport()!.render(fileId, patches)
    expect(res.svg).toBeUndefined()
    expect(res.preview?.mode).toBe('raster')
    await expect(engineTransport()!.previewPngUrl(fileId, patches, 800)).resolves.toBe(
      `data:image/png;base64,${PNG}`,
    )
  })

  it('矢量图照旧不取位图（显示走引擎 SVG）', async () => {
    const { fileId } = seedSession(openResult())
    restore = installMcpTransport(fakeBridge(() => okResult({})))
    await expect(engineTransport()!.previewPngUrl(fileId, [], 800)).rejects.toThrowError(
      EngineError,
    )
  })
})

describe('unwrap', () => {
  it('isError 转成带 code 的异常', () => {
    expect(() =>
      unwrap({ isError: true, structuredContent: { ok: false, code: 'x', error: '炸了' } }),
    ).toThrowError(EngineError)
  })

  it('ok:false 同样算失败——只看 isError 会把 bridge 的软失败当成成功', () => {
    expect(() => unwrap({ structuredContent: { ok: false, error: '炸了' } })).toThrow('炸了')
  })

  it('成功时原样交出结构化负载', () => {
    expect(unwrap({ structuredContent: { ok: true, session_id: 's-1' } })).toEqual({
      ok: true,
      session_id: 's-1',
    })
  })
})
