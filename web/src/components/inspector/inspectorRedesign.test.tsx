/**
 * 属性栏 / 画布栏重做（2026-10-07 设计审计 §9.2 / §9.3 / §9.4 P2）的行为契约：
 *   1. 行网格：恢复钮住在常驻的状态槽里（出现 / 消失不改控件那一格的内容）；修改点全部 = 实心、部分 = 空心环；
 *   2. ColorField 在属性栏里带可编辑 hex（回车提交、非法值退回）与取色面板（文档颜色），浮动栏不带；
 *   3. 身份头：「n 个问题 ›」直达问题面板（`openProblemAt`）；锁定状态胶囊点了就解锁；路径行常驻（两行固定）；
 *   4. 页签顺序 属性 | 画布 | 助手；没有选中时属性页是文档摘要卡，「画布设置 ›」切到画布页。
 * 选择器只认 data-*。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
}))
const openProblemAt = vi.fn()
vi.mock('@/lib/issueFocus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/issueFocus')>()),
  openProblemAt: (...args: unknown[]) => openProblemAt(...args),
}))

import { literal } from '@/i18n'
import type { ValidationIssue } from '@/lib/validation'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { emptyProject, type CanvasObject } from '@/types/document'
import { Row } from '../ui/Field'
import { ColorField } from '../ui/Input'
import { ColorFieldContext, resetRecentColors } from '../ui/colorPalette'
import { Inspector } from './Inspector'
import { ResetChip, labeledWithState } from './controls/textRows'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

let host: HTMLDivElement
let root: Root
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
  await act(async () => {})
}

const textObj = {
  id: 't1',
  type: 'text',
  text: '图注',
  x: 5,
  y: 5,
  w: 40,
  h: 8,
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  locked: true,
} as unknown as CanvasObject

async function seed(objects: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_redesign')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 100, h: 80 }
    d.objects = objects
  })
  useSelectionStore.getState().set(select)
}

const issue = (severity: ValidationIssue['severity'], objectId: string | null): ValidationIssue =>
  ({
    issueId: `${severity}-${objectId}`,
    ruleCode: 'font-size-min',
    severity,
    context: {},
    objectRef: { documentId: 'd', canvasId: 'c', objectId, gid: null },
    subject: { kind: 'object', objectType: 'text' },
    propertyPath: 'sizePt',
    message: literal('x'),
    technicalDetails: {},
    fixKind: 'none',
  }) as unknown as ValidationIssue

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  resetRecentColors()
  openProblemAt.mockReset()
  useValidationStore.setState({ issues: [] })
  useUiStore.setState({ rightTab: 'properties', elementPanelId: null, selectedGids: [] })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('行网格：状态槽与修改点', () => {
  it('恢复钮住在状态槽里，控件那一格只有控件；没改时槽位照样在', async () => {
    function Demo({ modified }: { modified: boolean }) {
      return (
        <Row
          labelWidth="grid"
          label={labeledWithState('线宽', modified)}
          status={modified ? <ResetChip label="线宽" onReset={() => {}} /> : undefined}
        >
          <input data-ctl />
        </Row>
      )
    }
    await render(<Demo modified={false} />)
    expect(q('[data-row-status]')).not.toBeNull()
    expect(q('[data-row-status] [data-reset-prop]')).toBeNull()
    expect(q('[data-modified-dot]')).toBeNull()
    await act(async () => root.render(<TooltipProvider><Demo modified /></TooltipProvider>))
    expect(q('[data-row-status] [data-reset-prop]')).not.toBeNull()
    // 控件那一格里只有控件：恢复钮出现不挤占它
    const control = q('[data-ctl]')!.parentElement!
    expect(control.querySelector('[data-reset-prop]')).toBeNull()
    expect(q('[data-modified-dot]')!.getAttribute('data-modified-dot')).toBe('all')
  })

  it('部分修改（多选里只改了几个）是空心环，全部修改是实心点', async () => {
    await render(
      <>
        <span data-a>{labeledWithState('颜色', 'some')}</span>
        <span data-b>{labeledWithState('颜色', 'all')}</span>
      </>,
    )
    const some = q('[data-a] [data-modified-dot]')!
    const all = q('[data-b] [data-modified-dot]')!
    expect(some.getAttribute('data-modified-dot')).toBe('some')
    expect(some.className).toContain('bg-transparent')
    expect(all.getAttribute('data-modified-dot')).toBe('all')
    expect(all.className).toContain('bg-ink')
  })
})

describe('ColorField：属性栏里的 hex 与取色面板', () => {
  it('宿主没挂取色面板（浮动栏）：只有色块，没有 hex 框', async () => {
    await render(<ColorField ariaLabel="颜色" value="#ff0000" onChange={() => {}} />)
    expect(q('[data-color-hex]')).toBeNull()
    expect(q('[data-color-swatch]')).toBeNull()
  })

  it('hex 回车提交（三位简写展开、小写），非法值退回原值；提交即报一次 onGestureEnd', async () => {
    const onChange = vi.fn()
    const onGestureEnd = vi.fn()
    await render(
      <ColorFieldContext.Provider value={{ rich: true, documentColors: [] }}>
        <ColorField ariaLabel="颜色" value="#ff0000" onChange={onChange} onGestureEnd={onGestureEnd} />
      </ColorFieldContext.Provider>,
    )
    const hex = q<HTMLInputElement>('[data-color-hex]')!
    expect(hex.value).toBe('#FF0000')
    const type = async (v: string) => {
      await act(async () => {
        hex.focus()
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
        set.call(hex, v)
        hex.dispatchEvent(new Event('input', { bubbles: true }))
      })
      await act(async () => {
        hex.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      })
    }
    await type('0AF')
    expect(onChange).toHaveBeenLastCalledWith('#00aaff')
    expect(onGestureEnd).toHaveBeenCalledTimes(1)
    onChange.mockClear()
    await type('not a colour')
    expect(onChange).not.toHaveBeenCalled()
    expect(hex.value).toBe('#FF0000')
  })

  it('点色块开取色面板：文档颜色一格一格，点一格就写下去', async () => {
    const onChange = vi.fn()
    await render(
      <ColorFieldContext.Provider value={{ rich: true, documentColors: ['#123456', '#abcdef'] }}>
        <ColorField ariaLabel="颜色" value="#ff0000" onChange={onChange} />
      </ColorFieldContext.Provider>,
    )
    const swatch = q<HTMLButtonElement>('[data-color-swatch]')!
    await act(async () => {
      swatch.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      swatch.click()
    })
    const chips = [...document.querySelectorAll('[data-color-group="document"] [data-color-chip]')]
    expect(chips.map((c) => c.getAttribute('data-color-chip'))).toEqual(['#123456', '#abcdef'])
    await act(async () => (chips[1] as HTMLElement).click())
    expect(onChange).toHaveBeenCalledWith('#abcdef')
  })

  it('disabled 是真禁用：色块、hex、系统取色盘都 disabled', async () => {
    await render(
      <ColorFieldContext.Provider value={{ rich: true, documentColors: [] }}>
        <ColorField ariaLabel="颜色" value="#ff0000" onChange={() => {}} disabled />
      </ColorFieldContext.Provider>,
    )
    expect(q<HTMLButtonElement>('[data-color-swatch]')!.disabled).toBe(true)
    expect(q<HTMLInputElement>('[data-color-hex]')!.disabled).toBe(true)
    expect(q<HTMLInputElement>('input[type="color"]')!.disabled).toBe(true)
  })
})

describe('身份头与右栏', () => {
  it('「n 个问题 ›」只数这一选择的错误 / 警告，点它走 openProblemAt（错误优先）', async () => {
    await seed([textObj], ['t1'])
    const warn = issue('warn', 't1')
    const err = issue('error', 't1')
    useValidationStore.setState({ issues: [warn, issue('suggestion', 't1'), issue('error', 'other'), err] })
    await render(<Inspector />)
    const chip = q<HTMLButtonElement>('[data-identity-problems]')!
    expect(chip.getAttribute('data-identity-problems')).toBe('2')
    await act(async () => chip.click())
    expect(openProblemAt).toHaveBeenCalledTimes(1)
    expect(openProblemAt.mock.calls[0][0]).toBe(err)
  })

  it('没有问题时没有这颗胶囊；路径行常驻（两行固定）', async () => {
    await seed([{ ...textObj, locked: false } as CanvasObject], ['t1'])
    await render(<Inspector />)
    expect(q('[data-identity-problems]')).toBeNull()
    expect(q('header[data-identity] [data-identity-path]')).not.toBeNull()
  })

  it('「已锁定」胶囊点了就解锁', async () => {
    await seed([textObj], ['t1'])
    await render(<Inspector />)
    await act(async () => q<HTMLButtonElement>('[data-state-chip="locked"]')!.click())
    expect(useDocumentStore.getState().doc.objects[0].locked).toBeFalsy()
    expect(q('[data-state-chip="locked"]')).toBeNull()
  })

  it('页签顺序：属性 | 画布 | 助手', async () => {
    await seed([textObj], ['t1'])
    await render(<Inspector />)
    const tabs = [...document.querySelectorAll('[data-inspector-tab]')].map((t) => t.getAttribute('data-inspector-tab'))
    expect(tabs).toEqual(['properties', 'canvas', 'assistant'])
  })

  it('没有选中：属性页是文档摘要卡，「画布设置 ›」切到画布页', async () => {
    await seed([textObj], [])
    await render(<Inspector />)
    const card = q('[data-document-summary]')!
    expect(card).not.toBeNull()
    await act(async () => q<HTMLButtonElement>('[data-document-summary-canvas]')!.click())
    expect(useUiStore.getState().rightTab).toBe('canvas')
  })
})
