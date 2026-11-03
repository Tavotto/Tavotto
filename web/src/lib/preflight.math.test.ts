/**
 * 排不出来的公式进问题面板（ADR 0152 §七）：spec 怎么填、面板怎么跟着排版结果重算。
 * 求值器本身的同源看护在 `preflight.golden.test.ts`（`canvas-text-math-unrenderable`）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { __resetMathForTests, mathBoxOf } from '@/lib/mathtext'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { cancelScheduled, startValidation, useValidationStore } from '@/store/validationStore'
import { emptyProject, type FigureDocument, type TextObject } from '@/types/document'
import { buildSpec } from './preflight'

const text = (over: Partial<TextObject> = {}): TextObject =>
  ({
    id: 't1',
    type: 'text',
    text: 'k = $\\bad{x}$ and $\\alpha$',
    textFormat: 'latex',
    sizePt: 9,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 5,
    y: 5,
    w: 40,
    h: 8,
    ...over,
  }) as TextObject

const docOf = (objects: TextObject[]): FigureDocument =>
  ({ page: { w: 80, h: 60 }, objects }) as unknown as FigureDocument

const noRender = { byKey: {}, latest: {} }

/** 后端替身：`\bad{x}` 排不出来，其余排得出来 */
function stubBackend() {
  vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body))
    return new Response(
      JSON.stringify({
        ok: true,
        results: body.items.map((it: { src: string }) =>
          it.src.includes('bad')
            ? { ok: false, error: 'Unknown symbol' }
            : { ok: true, width: 1, ascent: 0.7, descent: 0.1, segments: [['M', 0, 0], ['Z']] },
        ),
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  })
}

beforeEach(() => {
  __resetMathForTests()
  stubBackend()
})
afterEach(() => {
  cancelScheduled()
  vi.unstubAllGlobals()
})

describe('buildSpec：math_errors 只收缓存里 ok: false 的', () => {
  it('还没排出来 = 不算；排出来失败的才带进 spec，排得出的不带', async () => {
    const doc = docOf([text()])
    // 第一次：缓存是空的，「还不知道」不是「排不出」
    expect(buildSpec(doc, {}, noRender).texts[0].math_errors).toBeUndefined()
    await vi.waitFor(() => expect(mathBoxOf('\\bad{x}', 'serif')).toBeDefined())
    expect(buildSpec(doc, {}, noRender).texts[0].math_errors).toEqual([
      { src: '\\bad{x}', why: 'Unknown symbol' },
    ])
  })

  it('隐藏的文字、旧标记写法的文字都不带', async () => {
    const hidden = text({ id: 'h', hidden: true })
    const legacy = text({ id: 'l', textFormat: undefined })
    buildSpec(docOf([text()]), {}, noRender) // 把公式排进缓存
    await vi.waitFor(() => expect(mathBoxOf('\\bad{x}', 'serif')).toBeDefined())
    const spec = buildSpec(docOf([hidden, legacy]), {}, noRender)
    expect(spec.texts.map((t) => t.math_errors)).toEqual([undefined, undefined])
  })

  it('缓存按生效族分：衬线排不出不代表等宽也排不出', async () => {
    buildSpec(docOf([text()]), {}, noRender)
    await vi.waitFor(() => expect(mathBoxOf('\\bad{x}', 'serif')).toBeDefined())
    const mono = text({ fontFamily: 'monospace' })
    // 等宽那一条还没问过 → 不算
    expect(buildSpec(docOf([mono]), {}, noRender).texts[0].math_errors).toBeUndefined()
  })
})

describe('问题面板跟着排版结果重算', () => {
  it('排版回来前没有；回来后同一份文档自动出现 math-unrenderable，定位到那条文字', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_math')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 80, h: 60 }
      d.objects = [text()]
    })
    useAssetStore.setState({ byId: {} } as never)
    useValidationStore.setState({ results: [], issues: [], ready: false })
    const stop = startValidation()
    try {
      await vi.waitFor(
        () => {
          const hit = useValidationStore.getState().issues.find((i) => i.ruleCode === 'math-unrenderable')
          expect(hit?.objectRef.objectId).toBe('t1')
          expect(hit?.severity).toBe('warn')
        },
        { timeout: 3000 },
      )
    } finally {
      stop()
    }
  })
})
