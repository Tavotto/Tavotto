import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { engineLabel } from '@/components/inspector/roles/registry'
import { createPortal } from 'react-dom'
import { ExternalLink, Eye, EyeOff, Minus, Plus, RotateCcw } from 'lucide-react'
import { round4, scaleGroupAbout } from '@/lib/axesLayout'
import { geomTarget, positionOf } from '@/lib/elementGeom'
import type { EditableField, ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { clearOverrides, hideElement, setOverride, unhideElement } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useExactPanelManifest, usePanelDisplayManifest } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { propLabel } from '@/components/inspector/roles/registry'
import { ObjectContextMenu } from './ObjectContextMenu'
import { useQuickEdit } from './quickEditStore'
import { TextActionRow } from '@/components/inspector/TextActions'
import { hasTextStyleBar, TextStyleBar } from '@/components/inspector/TextStyleBar'
import { Button } from '@/components/ui/Button'
import { NumberField, TextArea } from '@/components/ui/Input'
import { LegendPositionPicker } from '@/components/inspector/controls/LegendPositionPicker'

/**
 * 右键快捷编辑：光标处的小弹层。
 *
 * 两种目标、两种外壳：
 *
 *   图内元素   → 本文件的 dialog-like 弹层：按角色给 3–6 个高频**控件**（文字框、
 *                样式条、缩放、图例位置），所以它是 `role="dialog"`，不是菜单；
 *   画布对象   → `ObjectContextMenu`：真正的上下文菜单（Radix：`role="menu"`、
 *                子菜单、方向键、越界翻转），按对象与选区给动作（Prompt 18）。
 *
 * 所有写入都走既有 actions（setOverride / 对象操作），因此天然进撤销、天然触发
 * 重渲染——这里只是把属性页里最常用的那几项搬到手边，不是第二套数据通道。
 * 开合状态在 `quickEditStore`（两种外壳同一个开关：ContextBar 让位、切工作流
 * 关闭、问题定位关闭都只认它一个）。
 */

const MARGIN = 8

/** 快捷编辑的文案（workspace:quickEdit.*） */
const qe = (key: string, values?: Record<string, unknown>) =>
  translate(`quickEdit.${key}`, { ns: 'workspace', ...(values ?? {}) })

export function QuickEdit() {
  const target = useQuickEdit((s) => s.target)
  const at = useQuickEdit((s) => s.at)
  const close = useQuickEdit((s) => s.close)

  // 画布一平移/缩放，锚点就失效了，直接关掉比跟随更诚实；窗口失焦同理。
  // 两种外壳共用这两条（Esc / 点外部由各自的外壳负责）
  useEffect(() => {
    if (!target) return
    window.addEventListener('wheel', close, { passive: true })
    window.addEventListener('blur', close)
    return () => {
      window.removeEventListener('wheel', close)
      window.removeEventListener('blur', close)
    }
  }, [target, close])

  if (!target) return null
  if (target.kind === 'object') return <ObjectContextMenu id={target.id} at={at} close={close} />
  return <ElementPopover target={target} at={at} close={close} />
}

/* -------------------------------------------------------------------------- */
/*  图内元素的弹层外壳                                                          */
/* -------------------------------------------------------------------------- */

function ElementPopover({
  target,
  at,
  close,
}: {
  target: { kind: 'element'; panelId: string; gid: string; focusText?: boolean }
  at: { x: number; y: number }
  close: () => void
}) {
  useTranslation('workspace')
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState(at)

  // 量出实际尺寸再贴边，避免弹层被视口切掉
  useLayoutEffect(() => {
    const el = ref.current
    const w = el?.offsetWidth ?? 200
    const h = el?.offsetHeight ?? 160
    setPos({
      x: Math.max(MARGIN, Math.min(at.x, window.innerWidth - w - MARGIN)),
      y: Math.max(MARGIN, Math.min(at.y, window.innerHeight - h - MARGIN)),
    })
    // 双击文字进来的直接聚焦内容框（全选便于整段替换）；
    // 其余情况焦点给容器，Tab 才能走到弹层里的控件
    const ta = target.focusText ? el?.querySelector('textarea') : null
    if (ta) {
      ta.focus({ preventScroll: true })
      ta.select()
    } else {
      el?.focus({ preventScroll: true })
    }
  }, [target, at])

  useEffect(() => {
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
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onDown, true)
    }
  }, [close])

  return createPortal(
    <div
      ref={ref}
      tabIndex={-1}
      // 含输入控件，按对话框宣告更诚实（纯菜单那份在 ObjectContextMenu）
      role="dialog"
      aria-label={qe('aria')}
      onContextMenu={(e) => e.preventDefault()}
      style={{ left: pos.x, top: pos.y }}
      className={cn(
        'fixed z-50 w-[268px] rounded-md border border-border bg-surface p-1',
        'text-xs text-ink shadow-pop animate-pop-in',
      )}
    >
      <ElementQuick target={target} close={close} />
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
  ...rest
}: {
  children: ReactNode
  shortcut?: string
  danger?: boolean
  onClick: () => void
} & Record<`data-${string}`, string | undefined>) {
  return (
    <button
      {...rest}
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
  useTranslation('workspace')
  const panel = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.id === target.panelId && o.type === 'panel'),
  ) as PanelObject | undefined
  const manifest = usePanelDisplayManifest(panel)
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
  // 「恢复此元素修改」：与属性页同一个 action、同一条历史标签，一次 commit
  const own = panel.overrides.filter((o) => o.gid === el.gid)
  const resetElement = () => {
    clearOverrides(
      panel.id,
      msg('element.resetElement', undefined, 'inspector'),
      own.map((o) => ({ gid: o.gid, prop: o.prop })),
    )
    close()
  }

  return (
    <>
      <Head>{el.label}</Head>

      {field('text') && <TextContentRow read={read} write={write} close={close} />}
      {hasTextStyleBar(el) && (
        <div className="px-1.5 py-1">
          <TextStyleBar panel={panel} element={el} />
        </div>
      )}
      {isGeometric(el) && <GeomControls panel={panel} el={el} />}
      {el.role === 'legend' && <LegendControls read={read} field={field} write={write} />}

      {(field('visible') || own.length > 0) && <Divider />}
      {own.length > 0 && (
        <Item onClick={resetElement} data-quick-item="reset-element">
          <span className="flex items-center gap-1.5">
            <RotateCcw size={12} />
            {translate('element.resetElementCount', { ns: 'inspector', count: own.length })}
          </span>
        </Item>
      )}
      {field('visible') && (
        <Item onClick={toggleVisible}>
          <span className="flex items-center gap-1.5">
            {hidden ? <Eye size={12} /> : <EyeOff size={12} />}
            {qe(hidden ? 'unhide' : 'hide')}
          </span>
        </Item>
      )}
      <Item onClick={openInPanel}>
        <span className="flex items-center gap-1.5">
          <ExternalLink size={12} />
          {qe('openInspector')}
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
    <div className="flex flex-col gap-1 px-1.5 py-0.5">
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
      {/* 换行 / 上下标 / 大小写：与属性页同一份组件，行为一字不差 */}
      <TextActionRow
        text={text}
        taRef={taRef}
        onChange={(next, immediate) => write('text', next, immediate)}
      />
    </div>
  )
}

/**
 * 子图与位图：绕中心缩放 + 占宽读数。
 * 位图自己没有几何属性，缩放落到它的宿主子图上（与画布上拖它一致）。
 *
 * 这是一条**几何写路径**（写 position override），所以 manifest 不吃调用方
 * 传下来的显示那份，自己取权威——`positionOf` 在没有 override 时会退回
 * manifest 里的 position 初值，那份要是上一版的，缩放就以别人的占位起算。
 * 权威没就位时整块不出现（issue #131）。
 */
function GeomControls({
  panel,
  el,
}: {
  panel: PanelObject
  el: ManifestElement
}) {
  useTranslation('workspace')
  const manifest = useExactPanelManifest(panel)
  const host = manifest ? geomTarget(manifest, el) : null
  const pos = host ? positionOf(panel, host) : null
  if (!manifest || !host || !pos) return null

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
      label={qe('scale')}
      hint={
        host.gid === el.gid
          ? qe('scaleTip')
          : qe('scaleProxiedTip', { label: engineLabel(host.label) })
      }
    >
      <Button size="icon-sm" aria-label={qe('scaleDown')} onClick={() => scale(0.95)}>
        <Minus size={12} />
      </Button>
      <Button size="icon-sm" aria-label={qe('scaleUp')} onClick={() => scale(1.05)}>
        <Plus size={12} />
      </Button>
      <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-ink-3">
        {qe('widthShare', { percent: Math.round(pos[2] * 100) })}
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
          {/* 与属性页同一个 3×3 位置网格（§16：同一概念同一控件） */}
          <LegendPositionPicker
            value={cur}
            options={loc.options}
            onChange={(v) => write('loc', v)}
            ariaLabel={propLabel('loc')}
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
const isGeometric = (el: ManifestElement) => !!el.resizable || !!el.geom_gid
