/**
 * 2026-10-07 设计刷新基础层的原语（宪法第五节 / 第二十六节）：Card、Notice、StatusPill、FormSection + FieldGroup、
 * SettingRow 的 layout、EmptyState v2、listRowClass 三档 + rowMetaClass + dropLineClass、RowMenu、Button 的 lg / danger-tinted。
 * 选择器只认 data-*（web/AGENTS.md）。类名断言只用来证明「走了哪一档 token」，不当行为判据。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingRow } from '../settings/SettingRow'
import { Button, IconButton } from './Button'
import { Card } from './Card'
import { EmptyState } from './EmptyState'
import { FieldGroup, FormSection } from './FormSection'
import { Pencil, Trash2 } from './icons'
import { dropLineClass, listRowClass, rowMetaClass } from './listRow'
import { MenuItem } from './Menu'
import { Notice } from './Notice'
import { RowMenu } from './RowMenu'
import { StatusPill } from './StatusPill'
import { TooltipProvider } from './Tooltip'
import { useRowMenu } from './useRowMenu'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const render = (node: React.ReactNode) => act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
const T = 'x'
const q = <E extends Element = HTMLElement>(sel: string) => document.querySelector<E>(sel as string) as E | null

describe('Card', () => {
  it('raised = 白底 + shadow-card、圆角 lg；padding 只有 none / sm / md 三档', async () => {
    await render(
      <>
        <Card data-c="a">{T}</Card>
        <Card data-c="b" appearance="subtle" padding="sm">{T}</Card>
        <Card data-c="c" appearance="plain" padding="none">{T}</Card>
      </>,
    )
    const a = q('[data-c="a"]')!
    expect(a.getAttribute('data-card')).toBe('raised')
    expect(a.className).toMatch(/\bshadow-card\b/)
    expect(a.className).toMatch(/\brounded-lg\b/)
    expect(a.className).toMatch(/\bp-3\b/)
    expect(q('[data-c="b"]')!.className).toMatch(/\bp-2\b/)
    expect(q('[data-c="b"]')!.className).not.toMatch(/shadow-card/)
    expect(q('[data-c="c"]')!.className).not.toMatch(/\bp-\d/)
  })

  it('交互态走 outline（几何不变）：interactive / selected 打 data-*，选中是 selected 底 + border-strong 轮廓', async () => {
    await render(
      <>
        <Card data-c="i" interactive>{T}</Card>
        <Card data-c="s" interactive selected>{T}</Card>
        <Card data-c="p">{T}</Card>
      </>,
    )
    const i = q('[data-c="i"]')!
    expect(i.hasAttribute('data-interactive')).toBe(true)
    expect(i.className).toMatch(/hover:outline-border\b/)
    expect(i.className).toMatch(/has-\[:focus-visible\]:ring-2/)
    const s = q('[data-c="s"]')!
    expect(s.hasAttribute('data-selected')).toBe(true)
    expect(s.className).toMatch(/\bbg-selected\b/)
    expect(s.className).toMatch(/\boutline-border-strong\b/)
    // 静态卡不画焦点环（里面的控件自己有）
    expect(q('[data-c="p"]')!.className).not.toMatch(/ring-2/)
  })
})

describe('Notice / StatusPill', () => {
  it.each(['neutral', 'info', 'ok', 'warn', 'danger'] as const)('Notice tone=%s：data-notice 落在根上，字走 -content（neutral 是 ink-2）', async (tone) => {
    await render(<Notice tone={tone} title={T} action={<Button data-n-action>{T}</Button>}>{T}</Notice>)
    const n = q(`[data-notice="${tone}"]`)!
    expect(n).not.toBeNull()
    expect(n.className).toMatch(/\brounded-md\b/)
    expect(n.className).toContain(tone === 'neutral' ? 'text-ink-2' : `text-${tone}-content`)
    expect(n.querySelector('[data-n-action]')).not.toBeNull()
    expect(n.getAttribute('role')).toBe(tone === 'danger' ? 'alert' : null)
  })

  it('StatusPill：20px 胶囊、500、锚点派生；dot 是 aria-hidden 的锚点色圆点', async () => {
    await render(<StatusPill tone="ok" dot>{T}</StatusPill>)
    const p = q('[data-status-pill="ok"]')!
    expect(p.className).toMatch(/\bh-5\b/)
    expect(p.className).toMatch(/\brounded-full\b/)
    expect(p.className).toMatch(/\bfont-medium\b/)
    expect(p.className).toContain('text-ok-content')
    const dot = p.querySelector('[aria-hidden]')!
    expect(dot.className).toContain('bg-ok')
  })
})

describe('FormSection + FieldGroup + SettingRow', () => {
  it('组里的 SettingRow 不再自带上下留白（内边距交给组），组外的照旧；balanced 是 4 : 6 两列、控件左起', async () => {
    await render(
      <FormSection title={T} description={T} data-fs>
        <FieldGroup data-fg>
          <SettingRow label={T} data-row="in">{T}</SettingRow>
          <SettingRow label={T} layout="balanced" data-row="bal">{T}</SettingRow>
        </FieldGroup>
        <SettingRow label={T} data-row="out">{T}</SettingRow>
      </FormSection>,
    )
    expect(q('[data-fs] h3')!.className).toMatch(/\bfont-semibold\b/)
    expect(q('[data-fg]')!.hasAttribute('data-field-group')).toBe(true)
    expect(q('[data-fg]')!.className).toMatch(/\brounded-lg\b/)
    expect(q('[data-fg]')!.className).toMatch(/\bbg-group\b/)
    expect(q('[data-row="in"]')!.className).not.toMatch(/\bpy-/)
    expect(q('[data-row="out"]')!.className).toMatch(/\bpy-2\.5\b/)
    const bal = q('[data-row="bal"]')!
    expect(bal.getAttribute('data-layout')).toBe('balanced')
    expect(bal.className).toContain('grid-cols-[minmax(0,4fr)_minmax(0,6fr)]')
    expect(bal.lastElementChild!.className).not.toMatch(/justify-end/)
  })
})

describe('EmptyState v2', () => {
  it('40px lg 圆角图标底座 + type-title 标题 + type-caption 说明（42ch）+ 32px 主动作', async () => {
    const onClick = vi.fn()
    await render(<EmptyState icon={Pencil} title={T} hint={T} action={{ label: T, onClick }} data-es />)
    const es = q('[data-es]')!
    expect(es.hasAttribute('data-empty-state')).toBe(true)
    const tile = es.firstElementChild!
    expect(tile.className).toMatch(/\bsize-10\b/)
    expect(tile.className).toMatch(/\brounded-lg\b/)
    expect(es.querySelector('.type-title')).not.toBeNull()
    expect(es.querySelector('.type-caption')!.className).toContain('max-w-[42ch]')
    const btn = es.querySelector<HTMLButtonElement>('button[data-variant]')!
    expect(btn.className).toMatch(/\bh-8\b/)
    expect(btn.getAttribute('data-variant')).toBe('secondary')
    await act(async () => btn.click())
    expect(onClick).toHaveBeenCalled()
  })
})

describe('listRowClass / rowMetaClass / dropLineClass', () => {
  it('三档高 28 / 44 / 52、圆角 md 8；选中 = selected 底 + 600', () => {
    expect(listRowClass()).toMatch(/\bh-7\b/)
    expect(listRowClass({ size: 'md' })).toMatch(/\bmin-h-11\b/)
    expect(listRowClass({ size: 'lg' })).toMatch(/\bmin-h-13\b/)
    for (const size of ['sm', 'md', 'lg'] as const) expect(listRowClass({ size })).toMatch(/\brounded-md\b/)
    expect(listRowClass({ selected: true })).toMatch(/\bbg-selected\b.*\bfont-semibold\b|\bfont-semibold\b.*\bbg-selected\b/)
    expect(listRowClass()).not.toMatch(/font-semibold/)
  })

  it('meta：常态 ink-3，选中升 ink-2，字重恒 400', () => {
    expect(rowMetaClass()).toMatch(/\btext-ink-3\b/)
    expect(rowMetaClass(true)).toMatch(/\btext-ink-2\b/)
    expect(rowMetaClass(true)).toMatch(/\bfont-normal\b/)
  })

  it('落点线：上 / 下缘 2px accent + 4px 圆点；没有落点时整个藏起来', () => {
    expect(dropLineClass('before')).toMatch(/-top-px/)
    expect(dropLineClass('after')).toMatch(/-bottom-px/)
    expect(dropLineClass('after')).toMatch(/\bh-0\.5\b.*\bbg-accent\b/)
    expect(dropLineClass('after')).toMatch(/before:size-1\b/)
    expect(dropLineClass(null)).toBe('hidden')
  })
})

describe('Button：lg 32 / 图标钮是圆 / danger-tinted / primary 字是 surface', () => {
  it('档位与层级落在 data-variant 与 token 类上', async () => {
    await render(
      <>
        <Button data-b="lg" size="lg" variant="primary">{T}</Button>
        <Button data-b="dt" size="lg" variant="danger-tinted">{T}</Button>
        <IconButton data-b="ic" label={T} tip={false}>
          <Pencil />
        </IconButton>
        <IconButton data-b="icl" label={T} tip={false} iconSize="lg">
          <Pencil />
        </IconButton>
      </>,
    )
    const lg = q('[data-b="lg"]')!
    expect(lg.className).toMatch(/\bh-8\b/)
    expect(lg.className).toMatch(/\btext-surface\b/)
    expect(lg.className).not.toMatch(/text-white/)
    const dt = q('[data-b="dt"]')!
    expect(dt.getAttribute('data-variant')).toBe('danger-tinted')
    expect(dt.className).toMatch(/\bbg-danger-surface\b/)
    expect(dt.className).toMatch(/\btext-danger-content\b/)
    expect(dt.className).toMatch(/\binset-ring-danger-border\b/)
    expect(dt.className).toMatch(/\bfont-semibold\b/)
    expect(q('[data-b="ic"]')!.className).toMatch(/\brounded-full\b/)
    expect(q('[data-b="icl"]')!.className).toMatch(/\bh-8 w-8\b/)
  })
})

function Row({ onRename }: { onRename: () => void }) {
  const menu = useRowMenu()
  return (
    <div data-row tabIndex={0} {...menu.rowProps} className={listRowClass()}>
      {T}
      <RowMenu state={menu} label={T}>
        <MenuItem data-mi="rename" icon={Pencil} onSelect={onRename}>
          {T}
        </MenuItem>
        <MenuItem data-mi="del" danger icon={Trash2}>
          {T}
        </MenuItem>
      </RowMenu>
    </div>
  )
}

describe('RowMenu：⋯ / 右键 / ⇧F10 同一份菜单', () => {
  const row = () => q('[data-row]')!
  const trigger = () => q<HTMLButtonElement>('[data-row-menu-trigger]')!

  it('⋯ 只有在行聚焦时才进 Tab 顺序', async () => {
    await render(<Row onRename={() => {}} />)
    expect(trigger().tabIndex).toBe(-1)
    await act(async () => row().focus())
    expect(trigger().tabIndex).toBe(0)
    await act(async () => {
      row().blur()
      document.body.focus()
    })
    expect(trigger().tabIndex).toBe(-1)
  })

  it('⇧F10 在行上打开从 ⋯ 垂下的菜单（与点 ⋯ 同一份清单），菜单项图标 ink-2、危险项跟字走红', async () => {
    const onRename = vi.fn()
    await render(<Row onRename={onRename} />)
    await act(async () => {
      row().dispatchEvent(new KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true, cancelable: true }))
    })
    expect(trigger().getAttribute('data-state')).toBe('open')
    const rename = q('[data-mi="rename"]')!
    expect(rename.querySelector('svg')!.getAttribute('class')).toContain('text-ink-2')
    expect(q('[data-mi="del"]')!.querySelector('svg')!.getAttribute('class')).toContain('text-danger')
  })

  it('右键在光标处开出同一份清单（PointMenu）', async () => {
    await render(<Row onRename={() => {}} />)
    await act(async () => {
      row().dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 10, clientY: 20 }))
    })
    const pm = q('[data-row-point-menu]')!
    expect(pm).not.toBeNull()
    expect(pm.querySelector('[data-mi="rename"]')).not.toBeNull()
  })
})
