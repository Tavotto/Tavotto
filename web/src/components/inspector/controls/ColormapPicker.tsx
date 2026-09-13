import { Check, ChevronDown } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { useState } from 'react'
import { t as translate } from '@/i18n'
import type { ColormapFacts } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Popover } from '../../ui/Popover'
import { colormapGradient } from './colormapStops'

/** 多选取值不一致时触发按钮上的占位文案（函数：常量会把语言定死在模块求值那一刻） */
const MIXED_TEXT = () => translate('element.mixedValues', { ns: 'inspector' })

/**
 * 色图在界面上叫什么：注册过的名字**保留原文**（viridis / RdBu_r 是 Matplotlib
 * 标识符，翻译反而对不上文档）；引擎说它是自定义的（`ListedColormap([...])` 那种
 * 没注册的，matplotlib 给的名字是 `from_list`）就叫「自定义」——那个名字对用户
 * 没有任何信息量，而且它根本不是一个能写进 override 的取值。原文留在可达名里。
 */
const displayName = (name: string, facts?: ColormapFacts | null) =>
  facts?.custom ? translate('control.customColormapName', { ns: 'inspector' }) : name

const accessibleName = (name: string, facts?: ColormapFacts | null) =>
  facts?.custom || !colormapGradient(name, facts)
    ? translate('control.customColormap', { ns: 'inspector', value: name })
    : name

/**
 * Colormap 选择器：真实渐变条（内置的 stops 离线采样自 matplotlib，白名单之外的
 * 由引擎随 manifest 发来 `cmap_current` / `cmap_original`），名称保留原文。
 *
 * 脚本自定义的 cmap（`from_list`）**不是可写的取值**：它只以「当前值」的身份出现，
 * 显示为「自定义」，选它 = 保持原样（不写 override）。换成别的色图之后，引擎发
 * `cmap_original`，列表顶上多一格「脚本原样」——选它走 `onRestore`（清掉 override）
 * 而不是写一条 `cmap: "from_list"`（那会当场「应用失败」）。
 */

function GradientBar({
  name,
  facts,
  className,
}: {
  name: string
  facts?: ColormapFacts | null
  className?: string
}) {
  const grad = colormapGradient(name, facts)
  if (!grad) {
    return (
      <span
        aria-hidden
        className={cn(
          'flex h-3.5 items-center justify-center rounded-xs border border-dashed border-border font-mono text-xs leading-none text-ink-3',
          className,
        )}
      >
        ?
      </span>
    )
  }
  return (
    <span
      aria-hidden
      data-cmap-gradient={facts?.discrete ? 'discrete' : 'smooth'}
      className={cn('block h-3.5 rounded-xs border border-border-strong/40', className)}
      style={{ background: grad }}
    />
  )
}

interface Entry {
  key: string
  name: string
  facts: ColormapFacts | null | undefined
  active: boolean
  /** 这一格的角色：写一条 override / 清掉 override（脚本原样）/ 什么都不做（已是当前值且写不进去） */
  action: 'write' | 'restore' | 'keep'
}

export function ColormapPicker({
  value,
  options,
  onChange,
  ariaLabel,
  current: currentFacts,
  original,
  onRestore,
}: {
  /**
   * 当前值。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  /** 写得进 override 的名字（引擎 `_cmap_options`）；当前值可以不在其中 */
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
  /** 当前值不在离线表里时它长什么样（manifest 的 `cmap_current`） */
  current?: ColormapFacts | null
  /** 换走之后脚本原来那张（manifest 的 `cmap_original`）；有它就多一格「脚本原样」 */
  original?: ColormapFacts | null
  /** 「脚本原样」那一格：清掉 override。没给就不出那一格 */
  onRestore?: () => void
}) {
  const [open, setOpen] = useState(false)
  // 事实只对得上名字才作数：override 刚写下、渲染还没回来的那一拍，`value` 已经是
  // 新名字而 manifest 里的事实还是上一张的，拿它画渐变条就是把上一张的色标画到
  // 新名字头上（还会把 viridis 叫成「自定义」）
  const current = currentFacts && currentFacts.name === value ? currentFacts : null
  const entries: Entry[] = []
  if (original && onRestore) {
    entries.push({
      key: '__original',
      name: original.name,
      facts: original,
      active: false,
      action: 'restore',
    })
  }
  if (value && !options.includes(value)) {
    // 当前值不在可写选项里：它是脚本自定义的（写不进 override）。摆出来是为了
    // 让用户看见「现在是这一张」，点它什么都不发生——从前这一格写出去的是
    // `cmap: "from_list"`，引擎当场 ValueError。
    entries.push({ key: value, name: value, facts: current, active: true, action: 'keep' })
  }
  for (const name of options) {
    entries.push({
      key: name,
      name,
      facts: name === value ? current : null,
      active: name === value,
      action: 'write',
    })
  }
  const originalTag = translate('control.scriptOriginal', { ns: 'inspector' })

  return (
    <Popover
      width={228}
      align="start"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          type="button"
          aria-label={ariaLabel}
          data-cmap-custom={current?.custom ? 'true' : undefined}
          className={cn(
            'flex h-7 w-full items-center gap-1.5 rounded-sm border border-transparent bg-surface-2 px-1.5',
            'text-xs text-ink outline-none transition-colors hover:border-border',
            'focus-visible:focus-ring',
            open && 'border-border-strong',
          )}
        >
          {value !== null && <GradientBar name={value} facts={current} className="w-12 shrink-0" />}
          <span
            className={cn('min-w-0 flex-1 truncate text-left', !current?.custom && 'font-mono')}
            title={value !== null && current?.custom ? value : undefined}
          >
            {value === null ? MIXED_TEXT() : displayName(value, current)}
          </span>
          <ChevronDown size={ICON_SIZE.xs} className="shrink-0 text-ink-3" />
        </button>
      }
    >
      <div
        role="radiogroup"
        aria-label={ariaLabel}
        className="flex max-h-72 flex-col gap-0.5 overflow-y-auto"
      >
        {entries.map((entry) => {
          const label = displayName(entry.name, entry.facts)
          const name =
            entry.action === 'restore'
              ? `${accessibleName(entry.name, entry.facts)} · ${originalTag}`
              : accessibleName(entry.name, entry.facts)
          return (
            <button
              key={entry.key}
              type="button"
              role="radio"
              aria-checked={entry.active}
              aria-label={name}
              data-cmap-entry={entry.action}
              onClick={() => {
                if (entry.action === 'write') onChange(entry.name)
                else if (entry.action === 'restore') onRestore?.()
                setOpen(false)
              }}
              className={cn(
                'flex h-7 items-center gap-2 rounded-sm border px-1.5 outline-none transition-colors',
                'focus-visible:focus-ring',
                entry.active
                  ? 'border-transparent bg-selected'
                  : 'border-transparent hover:bg-surface-hover',
              )}
            >
              <span className="flex w-3.5 shrink-0 items-center">
                {entry.active && <Check size={ICON_SIZE.xs} aria-hidden className="text-ink" />}
              </span>
              <GradientBar name={entry.name} facts={entry.facts} className="w-16 shrink-0" />
              <span
                className={cn(
                  'min-w-0 flex-1 truncate text-left text-xs text-ink',
                  !entry.facts?.custom && 'font-mono',
                )}
              >
                {label}
              </span>
              {entry.action === 'restore' && (
                <span className="shrink-0 text-xs text-ink-3">{originalTag}</span>
              )}
            </button>
          )
        })}
      </div>
    </Popover>
  )
}
