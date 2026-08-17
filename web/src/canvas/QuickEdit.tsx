import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Bold, ExternalLink, Eye, EyeOff, Minus, Plus } from 'lucide-react'
import { round4, scaleGroupAbout } from '@/lib/axesLayout'
import { geomTarget, positionOf } from '@/lib/elementGeom'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { cn, MOD } from '@/lib/utils'
import {
  changeZOrder,
  deleteSelected,
  duplicateSelected,
  enterElementEdit,
  hideElement,
  setOverride,
  toggleHidden,
  toggleLocked,
  unhideElement,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { CanvasObject, PanelObject } from '@/types/document'
import { objectLabel } from '@/types/document'
import { optionLabel, propLabel } from '@/components/inspector/roles/registry'
import { useQuickEdit } from './quickEditStore'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField, TextArea } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Toggle } from '@/components/ui/Toggle'

/**
 * 右键快捷编辑：光标处的小弹层。
 *
 * 图内元素按角色给 3–6 个高频控件，画布对象给一份轻量菜单。所有写入都走
 * 既有 actions（setOverride / 对象操作），因此天然进撤销、天然触发重渲染 ——
 * 这里只是把属性页里最常用的那几项搬到手边，不是第二套数据通道。
 */

const MARGIN = 8

export function QuickEdit() {
  const target = useQuickEdit((s) => s.target)
  const at = useQuickEdit((s) => s.at)
  const close = useQuickEdit((s) => s.close)
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState(at)

  // 量出实际尺寸再贴边，避免弹层被视口切掉
  useLayoutEffect(() => {
    if (!target) return
    const el = ref.current
    const w = el?.offsetWidth ?? 200
    const h = el?.offsetHeight ?? 160
    setPos({
      x: Math.max(MARGIN, Math.min(at.x, window.innerWidth - w - MARGIN)),
      y: Math.max(MARGIN, Math.min(at.y, window.innerHeight - h - MARGIN)),
    })
    // 双击文字进来的直接聚焦内容框（全选便于整段替换）；
    // 其余情况焦点给容器，Tab 才能走到弹层里的控件
    const ta = target.kind === 'element' && target.focusText
      ? el?.querySelector('textarea')
      : null
    if (ta) {
      ta.focus({ preventScroll: true })
      ta.select()
    } else {
      el?.focus({ preventScroll: true })
    }
  }, [target, at])

  useEffect(() => {
    if (!target) return
    // Esc 走捕获阶段：全局快捷键里的 Esc 另有职责（退编辑态），这里要先接住
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      close()
    }
    const onDown = (e: PointerEvent) => {
      const node = e.target as Element | null
      if (ref.current?.contains(node)) return
      // 下拉菜单（Select）挂在自己的 portal 里，点它不算点在弹层外面，
      // 否则选一次图例位置就会把弹层连同未提交的选择一起关掉
      if (node?.closest?.('[data-radix-popper-content-wrapper]')) return
      close()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onDown, true)
    // 画布一平移/缩放，锚点就失效了，直接关掉比跟随更诚实
    window.addEventListener('wheel', close, { passive: true })
    window.addEventListener('blur', close)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('wheel', close)
      window.removeEventListener('blur', close)
    }
  }, [target, close])

  if (!target) return null

  return createPortal(
    <div
      ref={ref}
      tabIndex={-1}
      // 对象那份是纯菜单，图内元素那份含输入控件，按对话框宣告更诚实
      role={target.kind === 'object' ? 'menu' : 'dialog'}
      aria-label="快捷编辑"
      onContextMenu={(e) => e.preventDefault()}
      style={{ left: pos.x, top: pos.y }}
      className={cn(
        'fixed z-50 w-[196px] rounded-md border border-border bg-surface p-1',
        'text-xs text-ink shadow-pop animate-pop-in',
      )}
    >
      {target.kind === 'element' ? (
        <ElementQuick target={target} close={close} />
      ) : (
        <ObjectQuick id={target.id} close={close} />
      )}
    </div>,
    document.body,
  )
}

/* -------------------------------------------------------------------------- */
/*  版式基元                                                                   */
/* -------------------------------------------------------------------------- */

function Head({ children }: { children: ReactNode }) {
  return (
    <div className="mb-0.5 truncate px-1.5 py-1 text-xs text-ink-3" title={String(children)}>
      {children}
    </div>
  )
}

function Line({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-6 items-center gap-1.5 px-1.5 py-0.5" title={hint}>
      <span className="w-11 shrink-0 truncate text-xs text-ink-2">{label}</span>
      <div className="flex min-w-0 flex-1 items-center gap-1">{children}</div>
    </div>
  )
}

function Item({
  children,
  shortcut,
  danger,
  onClick,
}: {
  children: ReactNode
  shortcut?: string
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex h-6 w-full cursor-default select-none items-center gap-3 rounded-sm px-2 text-xs',
        'outline-none hover:bg-ink/[.055] focus-visible:bg-ink/[.055]',
        danger ? 'text-danger' : 'text-ink',
      )}
    >
      <span className="flex-1 truncate text-left">{children}</span>
      {shortcut && <span className="font-mono text-xs text-ink-3">{shortcut}</span>}
    </button>
  )
}

const Divider = () => <div className="my-1 h-px bg-border" />

/* -------------------------------------------------------------------------- */
/*  图内元素                                                                   */
/* -------------------------------------------------------------------------- */

function ElementQuick({
  target,
  close,
}: {
  target: { panelId: string; gid: string; focusText?: boolean }
  close: () => void
}) {
  const panel = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.id === target.panelId && o.type === 'panel'),
  ) as PanelObject | undefined
  const manifest = useRenderStore((s) => (panel ? s.byFile[panel.fileId]?.manifest : null))
  const el = manifest?.elements.find((e) => e.gid === target.gid)

  useEffect(() => {
    if (!panel || !el) close()
  }, [panel, el, close])
  if (!panel || !el || !manifest) return null

  /** 当前值：用户改过的 override 优先于渲染时的初值 */
  const read = (prop: string, from: ManifestElement = el) => {
    const ov = panel.overrides.find((o) => o.gid === from.gid && o.prop === prop)
    if (ov) return ov.value
    return from.editable.find((f) => f.prop === prop)?.value
  }
  const field = (prop: string, from: ManifestElement = el): EditableField | undefined =>
    from.editable.find((f) => f.prop === prop)
  const write = (prop: string, value: unknown, immediate = true) =>
    setOverride(panel.id, el.gid, prop, value, immediate)

  const openInPanel = () => {
    useUiStore.getState().setSelectedGid(el.gid)
    useUiStore.getState().setRightTab('properties')
    close()
  }

  const hidden = read('visible') === false
  const toggleVisible = () => {
    if (hidden) unhideElement(panel.id, el.gid)
    else hideElement(panel.id, el.gid, el.label)
    close()
  }

  return (
    <>
      <Head>{el.label}</Head>

      {field('text') && <TextContentRow read={read} write={write} close={close} />}
      {isTextLike(el) && <TextControls read={read} field={field} write={write} />}
      {isGeometric(el) && <GeomControls panel={panel} el={el} manifest={manifest} />}
      {el.role === 'legend' && <LegendControls read={read} field={field} write={write} />}

      {field('visible') && (
        <>
          <Divider />
          <Item onClick={toggleVisible}>
            <span className="flex items-center gap-1.5">
              {hidden ? <Eye size={12} /> : <EyeOff size={12} />}
              {hidden ? '恢复显示' : '隐藏此元素'}
            </span>
          </Item>
        </>
      )}
      <Item onClick={openInPanel}>
        <span className="flex items-center gap-1.5">
          <ExternalLink size={12} />
          在属性页打开
        </span>
      </Item>
    </>
  )
}

/**
 * 文字内容：双击图内文字元素的主入口。契约与画布文字/属性页一致：
 * Enter 提交并关闭，⌥/⌘/Ctrl+Enter 换行，Esc 由弹层统一接管。
 */
function TextContentRow({
  read,
  write,
  close,
}: {
  read: (prop: string) => unknown
  write: (prop: string, value: unknown, immediate?: boolean) => void
  close: () => void
}) {
  const text = String(read('text') ?? '')
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  const insertNewline = () => {
    const ta = taRef.current
    const s = ta?.selectionStart ?? text.length
    const t = ta?.selectionEnd ?? text.length
    write('text', text.slice(0, s) + '\n' + text.slice(t), false)
    requestAnimationFrame(() => {
      const el = taRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(s + 1, s + 1)
      }
    })
  }

  return (
    <div className="px-1.5 py-0.5">
      <TextArea
        ref={taRef}
        rows={Math.min(3, text.split('\n').length)}
        value={text}
        onChange={(e) => write('text', e.target.value, false)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') {
            e.preventDefault()
            if (e.altKey || e.metaKey || e.ctrlKey) insertNewline()
            else close()
          }
        }}
      />
    </div>
  )
}

/** 文字类：字号 / 加粗 / 颜色 / 背景开关 + 底色 —— 每项都要 manifest 里真有才出现 */
function TextControls({
  read,
  field,
  write,
}: {
  read: (prop: string) => unknown
  field: (prop: string) => EditableField | undefined
  write: (prop: string, value: unknown, immediate?: boolean) => void
}) {
  const size = field('fontsize')
  const weight = field('weight')
  const color = field('color')
  const bbox = field('bbox_visible')
  const bboxColor = field('bbox_facecolor')
  const bold = read('weight') === 'bold'
  const bgOn = read('bbox_visible') === true

  return (
    <>
      {size && (
        <Line label={propLabel('fontsize')}>
          <NumberField
            className="min-w-0 flex-1"
            value={Number(read('fontsize') ?? 9)}
            step={size.step ?? 0.5}
            min={size.min ?? 1}
            max={size.max ?? 96}
            precision={1}
            onChange={(v) => write('fontsize', v, false)}
          />
          {weight && (
            <Button
              size="icon-sm"
              active={bold}
              aria-pressed={bold}
              aria-label={propLabel('weight')}
              onClick={() => write('weight', bold ? 'normal' : 'bold')}
            >
              <Bold size={12} />
            </Button>
          )}
        </Line>
      )}
      {color && (
        <Line label={propLabel('color')}>
          <ColorField
            className="min-w-0 flex-1"
            value={String(read('color') ?? '#000000')}
            onChange={(v) => write('color', v)}
          />
        </Line>
      )}
      {bbox && (
        <Line label={propLabel('bbox_visible')}>
          <Toggle checked={bgOn} onChange={(v) => write('bbox_visible', v)} />
          {bgOn && bboxColor && (
            <ColorField
              className="min-w-0 flex-1"
              value={String(read('bbox_facecolor') ?? '#ffffff')}
              onChange={(v) => write('bbox_facecolor', v)}
            />
          )}
        </Line>
      )}
    </>
  )
}

/**
 * 子图与位图：绕中心缩放 + 占宽读数。
 * 位图自己没有几何属性，缩放落到它的宿主子图上（与画布上拖它一致）。
 */
function GeomControls({
  panel,
  el,
  manifest,
}: {
  panel: PanelObject
  el: ManifestElement
  manifest: Manifest
}) {
  const host = geomTarget(manifest, el)
  const pos = positionOf(panel, host)
  if (!pos) return null

  const scale = (k: number) =>
    setOverride(
      panel.id,
      host.gid,
      'position',
      scaleGroupAbout(pos, k).map(round4),
      true,
    )

  return (
    <Line
      label="缩放"
      hint={
        host.gid === el.gid
          ? '绕子图中心缩放，每次 5%'
          : `位置和大小属于宿主子图「${host.label}」，缩放改的是它`
      }
    >
      <Button size="icon-sm" aria-label="缩小 5%" onClick={() => scale(0.95)}>
        <Minus size={12} />
      </Button>
      <Button size="icon-sm" aria-label="放大 5%" onClick={() => scale(1.05)}>
        <Plus size={12} />
      </Button>
      <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-ink-3">
        占宽 {Math.round(pos[2] * 100)}%
      </span>
    </Line>
  )
}

/** 图例：位置预设 + 字号 */
function LegendControls({
  read,
  field,
  write,
}: {
  read: (prop: string) => unknown
  field: (prop: string) => EditableField | undefined
  write: (prop: string, value: unknown, immediate?: boolean) => void
}) {
  const loc = field('loc')
  const size = field('fontsize')
  const cur = String(read('loc') ?? 'best')

  return (
    <>
      {!!loc?.options?.length && (
        <Line label={propLabel('loc')}>
          <Select
            className="min-w-0 flex-1"
            ariaLabel={propLabel('loc')}
            value={loc.options.includes(cur) ? cur : loc.options[0]}
            onChange={(v) => write('loc', v)}
            options={loc.options.map((o) => ({ value: o, label: optionLabel('loc', o) }))}
          />
        </Line>
      )}
      {size && (
        <Line label={propLabel('fontsize')}>
          <NumberField
            className="min-w-0 flex-1"
            value={Number(read('fontsize') ?? 8)}
            step={size.step ?? 0.5}
            min={size.min ?? 1}
            max={size.max ?? 96}
            precision={1}
            onChange={(v) => write('fontsize', v, false)}
          />
        </Line>
      )}
    </>
  )
}

/** 有字号又有颜色的元素就按文字对待——比枚举角色名更耐引擎变动 */
const isTextLike = (el: ManifestElement) =>
  el.role !== 'legend' &&
  el.editable.some((f) => f.prop === 'fontsize') &&
  el.editable.some((f) => f.prop === 'color')

const isGeometric = (el: ManifestElement) => !!el.resizable || !!el.geom_gid

/* -------------------------------------------------------------------------- */
/*  画布对象                                                                   */
/* -------------------------------------------------------------------------- */

function ObjectQuick({ id, close }: { id: string; close: () => void }) {
  const obj = useDocumentStore((s) => s.doc.objects.find((o) => o.id === id)) as
    | CanvasObject
    | undefined
  const selected = useSelectionStore((s) => s.ids.length)

  useEffect(() => {
    if (!obj) close()
  }, [obj, close])
  if (!obj) return null

  // 动作抛异常时菜单也必须关掉：卡在屏幕上的菜单比错误本身更让人摸不着头脑。
  // 异常继续往外抛（该进 Console / ErrorBoundary 的还得进）。
  const run = (fn: () => void) => () => {
    try {
      fn()
    } finally {
      close()
    }
  }

  return (
    <>
      {/* 复制 / 层级 / 删除作用于整个选区，多选时要说清楚，别让人以为只动这一个 */}
      <Head>{selected > 1 ? `已选 ${selected} 个对象` : objectLabel(obj)}</Head>
      {obj.type === 'panel' && obj.script && (
        <Item onClick={run(() => enterElementEdit(obj.id))}>编辑图内元素</Item>
      )}
      <Item onClick={run(duplicateSelected)} shortcut={`${MOD}D`}>
        复制
      </Item>
      <Item onClick={run(() => toggleLocked(obj.id))}>{obj.locked ? '解锁' : '锁定'}</Item>
      <Item onClick={run(() => toggleHidden(obj.id))}>{obj.hidden ? '显示' : '隐藏'}</Item>
      <Divider />
      <Item onClick={run(() => changeZOrder('top'))} shortcut={`⇧${MOD}]`}>
        置于顶层
      </Item>
      <Item onClick={run(() => changeZOrder('up'))} shortcut={`${MOD}]`}>
        上移一层
      </Item>
      <Item onClick={run(() => changeZOrder('down'))} shortcut={`${MOD}[`}>
        下移一层
      </Item>
      <Item onClick={run(() => changeZOrder('bottom'))} shortcut={`⇧${MOD}[`}>
        置于底层
      </Item>
      <Divider />
      <Item danger onClick={run(deleteSelected)} shortcut="Delete">
        删除
      </Item>
    </>
  )
}
