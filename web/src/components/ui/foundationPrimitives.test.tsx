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
import { readFileSync } from 'node:fs'
import path from 'node:path'
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

  it('按压只换底色（几何永不变化）：interactive 卡带 press 类，类名里没有 scale / translate / 布局属性的 transition', async () => {
    await render(
      <>
        <Card data-c="i" interactive>{T}</Card>
        <Card data-c="s" interactive selected>{T}</Card>
        <Card data-c="d" interactive disabled>{T}</Card>
        <Card data-c="p">{T}</Card>
      </>,
    )
    const i = q('[data-c="i"]')!
    expect(i.className).toMatch(/has-\[>button:active:not\(:disabled\)\]:bg-surface-active/)
    for (const sel of ['i', 's', 'd', 'p']) {
      const cls = q(`[data-c="${sel}"]`)!.className
      expect(cls, sel).not.toMatch(/scale|translate|rotate|-skew/)
      // transition 只许是颜色类的属性；不得出现 width / height / padding / margin / transform / all
      const tr = cls.match(/transition-\[([^\]]*)\]/)?.[1] ?? ''
      expect(tr, sel).not.toMatch(/width|height|padding|margin|transform|inset|top|left|all/)
    }
    // 选中的卡底色另有主人，静态卡无交互
    expect(q('[data-c="s"]')!.className).not.toMatch(/bg-surface-active/)
    expect(q('[data-c="p"]')!.className).not.toMatch(/bg-surface-active/)
  })

  it('lift 是登记豁免（用户 2026-10-15 裁决）：位移只许整数 px 的 translateY ≤2px、缩放 ≤1.02、光晕只许 1px 品牌蓝描边（无外发光）/ will-change / filter；没开 lift 的卡仍不位移不缩放', async () => {
    await render(
      <>
        <Card data-c="n" interactive>{T}</Card>
        <Card data-c="l" interactive lift>{T}</Card>
        <Card data-c="ld" interactive lift disabled>{T}</Card>
        <Card data-c="ls" interactive lift selected>{T}</Card>
      </>,
    )
    // 非 lift：沿用「几何默认不变」——类名里没有任何位移 / 缩放 / 升档阴影
    expect(q('[data-c="n"]')!.className).not.toMatch(/translate|scale|shadow-lift/)
    const lift = q('[data-c="l"]')!.className
    const tokens = lift.split(/\s+/)
    const moves = tokens.filter((t) => /(^|:)-?translate-/.test(t)).map((t) => t.replace(/^.*:/, ''))
    const scales = tokens.filter((t) => /(^|:)scale-/.test(t)).map((t) => t.replace(/^.*:/, ''))
    expect(moves.length).toBeGreaterThan(0)
    expect(scales.length).toBeGreaterThan(0)
    // 每一个位移都是 Y 轴的 -px（1px）/ -0.5（2px）/ 0（回落）：整数像素、上限 2px
    for (const m of moves) expect(m, m).toMatch(/^(-translate-y-(px|0\.5)|translate-y-0)$/)
    // 缩放只许两种写法：scale-100（静止 / 按下）与读 --card-lift-scale 的变量；不许写死任意倍数
    for (const m of scales) expect(m, m).toMatch(/^(scale-100|scale-\(--card-lift-scale\))$/)
    // --card-lift-scale 的值 ≤1.02（1.05 之类会让文字栅格化变糊、盖住邻卡）
    const css = readFileSync(path.resolve(import.meta.dirname, '../../index.css'), 'utf8')
    const v = Number(css.match(/--card-lift-scale:\s*([\d.]+)\s*;/)?.[1])
    expect(v, '--card-lift-scale').toBeGreaterThan(1)
    expect(v, '--card-lift-scale 上限 1.02').toBeLessThanOrEqual(1.02)
    // 描边：--lift-halo 是 accent 20% 的 1px 环（零模糊），不是外发光（用户 2026-10-16 选定方案 B）
    expect(css.match(/--lift-halo:\s*([^;]+);/)?.[1].replace(/\s+/g, ' ')).toBe(
      '0 0 0 1px color-mix(in srgb, var(--color-accent) 20%, transparent)',
    )
    // 位移与缩放只在 motion-safe 下有（reduced-motion 只换阴影 / 描边）
    for (const t of tokens) if (/(hover|focus-visible):.*(translate|scale)-/.test(t)) expect(t, t).toMatch(/^motion-safe:/)
    expect(lift).toMatch(/hover:shadow-lift/)
    expect(lift).toMatch(/motion-safe:has-\[:focus-visible\]:-translate-y-0\.5/) // 键盘聚焦同样浮起
    // 静止态恒等变换（不是 none：none ↔ 非 none 会让每次 hover 重排一次）
    expect(tokens).toContain('translate-y-0')
    expect(tokens).toContain('scale-100')
    // 按下回落：位移 0、缩放 1
    expect(lift).toMatch(/has-\[>button:active:not\(:disabled\)\]:translate-y-0/)
    expect(lift).toMatch(/has-\[>button:active:not\(:disabled\)\]:scale-100/)
    expect(lift).not.toMatch(/rotate|skew|will-change|blur|filter|gradient/)
    // 禁用 / 选中的卡不浮起
    for (const sel of ['ld', 'ls'])
      expect(q(`[data-c="${sel}"]`)!.className.replace(/transition-\[[^\]]*\]/, ''), sel).not.toMatch(/translate|scale|shadow-lift/)
  })

  it('disabled：写 data-disabled，不亮 hover / press / 焦点环；interactive 标记仍在', async () => {
    await render(
      <>
        <Card data-c="d" interactive disabled>{T}</Card>
        <Card data-c="p" disabled>{T}</Card>
      </>,
    )
    const d = q('[data-c="d"]')!
    expect(d.dataset.disabled).toBe('true')
    expect(d.dataset.interactive).toBe('true')
    expect(d.className).not.toMatch(/hover:outline-border/)
    expect(d.className).not.toMatch(/bg-surface-active/)
    expect(d.className).not.toMatch(/ring-2/)
    // 非 interactive 的卡没有可禁用的交互外观，不写 data-disabled
    expect(q('[data-c="p"]')!.hasAttribute('data-disabled')).toBe(false)
  })
})

describe('Notice / StatusPill', () => {
  it.each(['neutral', 'info', 'ok', 'warn', 'danger'] as const)('Notice tone=%s：data-notice 落在根上，8 圆角、中性底、无彩框，role 不随改版变', async (tone) => {
    await render(<Notice tone={tone} title={T} action={<Button data-n-action>{T}</Button>}>{T}</Notice>)
    const n = q(`[data-notice="${tone}"]`)!
    expect(n).not.toBeNull()
    expect(n.className).toMatch(/\brounded-md\b/)
    expect(n.querySelector('[data-n-action]')).not.toBeNull()
    expect(n.getAttribute('role')).toBe(tone === 'danger' ? 'alert' : null)
  })

  it('StatusPill：20px 胶囊、500、中性底、文字 ink；语义色只在 6px 圆点（aria-hidden）上', async () => {
    await render(
      <>
        <StatusPill tone="ok" data-sp="ok">{T}</StatusPill>
        <StatusPill data-sp="n">{T}</StatusPill>
      </>,
    )
    const p = q('[data-sp="ok"]')!
    expect(p.className).toMatch(/\bh-5\b/)
    expect(p.className).toMatch(/\brounded-full\b/)
    expect(p.className).toMatch(/\bfont-medium\b/)
    expect(p.className).toContain('bg-surface-hover')
    expect(p.className).toContain('text-ink')
    expect(p.className).not.toMatch(/(?:ok|warn|danger|info)-(?:surface|content|border)/)
    const dot = p.querySelector('[aria-hidden]')!
    expect(dot.className).toContain('bg-ok')
    // 中性（版本号 / 选区这类数据）不画点
    expect(q('[data-sp="n"] [aria-hidden]')).toBeNull()
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
    expect(q('[data-fg]')!.hasAttribute('data-ui-field-group')).toBe(true)
    // 设置页早有 data-field-group="fonts" 这类分组：原语的样式钩子另起名，不让 index.css 的规则误中它们
    expect(q('[data-fg]')!.hasAttribute('data-field-group')).toBe(false)
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
    // 结构侧栏变体：圆角 6；叶子 ink-2、分组头 500 + ink、选中 600 + ink
    expect(listRowClass({ tree: true })).toMatch(/\brounded-sm\b/)
    expect(listRowClass({ tree: true })).toMatch(/\btext-ink-2\b/)
    expect(listRowClass({ tree: true, group: true })).toMatch(/\bfont-medium\b.*\btext-ink\b|\btext-ink\b.*\bfont-medium\b/)
    expect(listRowClass({ tree: true, selected: true })).toMatch(/\bfont-semibold\b/)
    expect(listRowClass({ tree: true, selected: true })).not.toMatch(/text-ink-2/)
    expect(listRowClass()).toMatch(/\brounded-md\b/)
    // 被拖行淡化：用 opacity-dragging，且压过 hidden 的 45
    expect(listRowClass({ dragging: true })).toMatch(/\bopacity-dragging\b/)
    expect(listRowClass({ dragging: true, hidden: true })).not.toMatch(/opacity-45/)
    expect(listRowClass()).not.toMatch(/opacity-dragging/)
    // 焦点环内凹：密排的行不许用外扩的 focus-ring（会盖邻行、触抽屉边）
    for (const size of ['sm', 'md', 'lg'] as const) {
      expect(listRowClass({ size })).toMatch(/\bfocus-visible:focus-ring-inset\b/)
      expect(listRowClass({ size })).not.toMatch(/focus-visible:focus-ring(?!-inset)/)
    }
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
