import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Check, ChevronDown } from './icons'
import { ICON_SIZE } from './Icon'
import { Popover } from './Popover'
import { SearchInput } from './SearchInput'
import { SEARCH_MIN_ITEMS } from './searchThreshold'
import { SELECT_CHEVRON, SELECT_ROW, SELECT_TRIGGER, type SelectOption } from './Select'
import { cn } from '@/lib/utils'

/** 比对用的规范形：不分大小写、不看空格（`timesnew` 找得到 Times New Roman） */
const norm = (s: string) => s.toLocaleLowerCase().replace(/\s+/g, '')

const textOf = (o: SelectOption<string>) =>
  o.searchText ?? (typeof o.label === 'string' ? o.label : String(o.value))

/** 按搜索词筛选项；空词 = 全部，顺序不变。 */
export function filterOptions<T extends string>(options: SelectOption<T>[], query: string): SelectOption<T>[] {
  const q = norm(query)
  return q ? options.filter((o) => norm(textOf(o)).includes(q)) : options
}

interface SearchSelectProps<T extends string> {
  value: T
  onChange: (v: T) => void
  options: SelectOption<T>[]
  /** 搜索框的可达名与占位 */
  searchLabel: string
  /** 筛空时的一句话 */
  emptyLabel: string
  className?: string
  placeholder?: string
  disabled?: boolean
  ariaLabel?: string
  title?: string
  triggerLabel?: ReactNode
  prefix?: ReactNode
}

/**
 * 带搜索框的下拉：选项上百、靠滚动找不到时用（字体）。收起时的样子与 `Select` 一模一样。
 *
 * 不是在 Radix Select 里塞输入框：它的条目一悬停就抢焦点，字打到一半就断了。这里焦点一直在搜索框，
 * ↑↓ 移高亮、回车选中、Esc 关闭。另一个好处是收起时不渲染选项：Radix Select 关着也重建全部条目，
 * 字体并表几百项时那是属性页最贵的一笔（`FontFamilyRow` 的 memo 就是为它写的）。
 */
export function SearchSelect<T extends string>({
  value,
  onChange,
  options,
  searchLabel,
  emptyLabel,
  className,
  placeholder,
  disabled,
  ariaLabel,
  title,
  triggerLabel,
  prefix,
}: SearchSelectProps<T>) {
  const [open, setOpen] = useState(false)
  const selected = options.find((o) => o.value === value)
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="start"
      width={{ min: 240 }}
      trigger={
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          data-select-trigger
          data-state={open ? 'open' : 'closed'}
          disabled={disabled}
          title={title}
          className={cn(SELECT_TRIGGER, className)}
        >
          {prefix != null && (
            <span aria-hidden data-select-prefix className="shrink-0 select-none whitespace-nowrap text-ink-3">
              {prefix}
            </span>
          )}
          <span className="min-w-0 flex-1 truncate text-left">{triggerLabel ?? selected?.label ?? placeholder}</span>
          <ChevronDown size={ICON_SIZE.xs} className={SELECT_CHEVRON} />
        </button>
      }
    >
      <SearchList
        value={value}
        options={options}
        searchLabel={searchLabel}
        emptyLabel={emptyLabel}
        ariaLabel={ariaLabel}
        onPick={(v) => {
          setOpen(false)
          if (v !== value) onChange(v)
        }}
      />
    </Popover>
  )
}

/** 弹层内容：打开时才挂载，搜索词与高亮随关闭一起没了。 */
function SearchList<T extends string>({
  value,
  options,
  searchLabel,
  emptyLabel,
  ariaLabel,
  onPick,
}: {
  value: T
  options: SelectOption<T>[]
  searchLabel: string
  emptyLabel: string
  ariaLabel?: string
  onPick: (v: T) => void
}) {
  const [query, setQuery] = useState('')
  const shown = useMemo(() => filterOptions(options, query), [options, query])
  const [active, setActive] = useState(() => Math.max(0, shown.findIndex((o) => o.value === value)))
  const listRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    listRef.current?.querySelector('[data-active]')?.scrollIntoView?.({ block: 'nearest' })
  }, [active, shown])

  const onKeyDown = (e: KeyboardEvent) => {
    // 输入法组字中：回车是确认候选词、方向键是在候选里选，不能被弹层抢走（229 兼容 Safari 等老事件）
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (shown.length) setActive((a) => (a + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const hit = shown[active]
      if (hit) onPick(hit.value)
    }
  }

  const activeId = (i: number) => `search-select-${i}`
  return (
    <div onKeyDown={onKeyDown} className="flex flex-col gap-1">
      {(options.length >= SEARCH_MIN_ITEMS || query) && (
        <SearchInput
          data-select-search
          value={query}
          onValueChange={(q) => {
            setQuery(q)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          role="searchbox"
          aria-label={searchLabel}
          aria-activedescendant={shown.length ? activeId(active) : undefined}
          placeholder={searchLabel}
          autoComplete="off"
          spellCheck={false}
        />
      )}
      <div ref={listRef} role="listbox" aria-label={ariaLabel} tabIndex={0} className="max-h-72 overflow-y-auto outline-none">
        {shown.map((o, i) => (
          <div
            key={o.value}
            id={activeId(i)}
            role="option"
            aria-selected={o.value === value}
            data-select-option={o.value}
            data-active={i === active ? '' : undefined}
            // 焦点留在搜索框，点选不把它带走（否则键盘接不上）
            onMouseDown={(e) => e.preventDefault()}
            onPointerMove={() => setActive(i)}
            onClick={() => onPick(o.value)}
            className={cn(SELECT_ROW, 'data-[active]:bg-surface-hover', o.value === value && 'font-medium')}
          >
            {o.value === value && (
              <span className="absolute left-1.5 flex items-center">
                <Check size={ICON_SIZE.sm} />
              </span>
            )}
            <span className="min-w-0 truncate">{o.label}</span>
          </div>
        ))}
        {shown.length === 0 && <div className="px-2 py-1.5 text-sm text-ink-3">{emptyLabel}</div>}
      </div>
    </div>
  )
}
