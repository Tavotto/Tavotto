import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { logoFor } from '@/components/ai/ModelPicker'
import { BrandLogo, hasBrandLogo } from '@/components/settings/BrandLogo'
import { ICON_SIZE } from '@/components/ui/Icon'
import { IconButton } from '@/components/ui/Button'
import { Plus, Sparkles, Unplug } from '@/components/ui/icons'
import { Menu, MenuItem, MenuRadioGroup, MenuRadioItem, MenuSeparator } from '@/components/ui/Menu'
import { backendErrorMsg, ApiError, type AiDataUnderstandingChoice } from '@/lib/api'
import { msg } from '@/i18n'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { requestConnectAi } from '@/store/connectAiStore'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 「不用 AI 分析」的图标：✦ 上一道贯穿整个图标的对角斜线（满幅 3,3 → 21,21，线宽 2；斜线底下垫一道纸色粗线把星光切开，免得糊在一起）。
 * 按钮的关闭态与列表里「不用 AI 分析」那一行是同一个。
 */
function OffIcon({ size = ICON_SIZE.md, className }: { size?: number; className?: string }) {
  return (
    <span className={cn('relative inline-flex', className)} style={{ width: size, height: size }}>
      <Sparkles size={size} aria-hidden />
      <svg aria-hidden viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" className="absolute inset-0">
        <path d="M3 3L21 21" strokeWidth="4.5" className="stroke-surface" />
        <path d="M3 3L21 21" strokeWidth="2" stroke="currentColor" />
      </svg>
    </span>
  )
}

/** 列表里的取值：`auto` / `off` / `conn:<连接 id>` */
const valueOf = (c: AiDataUnderstandingChoice): string => (c.mode === 'connection' && c.chosen ? `conn:${c.chosen}` : c.mode)
const CONN = 'conn:'

/**
 * 「读懂数据」用哪个 AI 的小图标（左栏「数据」抽屉左下角，浮着、不占布局高度）：点开列表选「自动 / 某个 AI / 不用 AI 分析」，另有「连接其他 AI…」。
 * 三种状态靠**形状**区分，不只靠颜色：在用 = 那个 AI 的厂商图标（没有就 ✦）；关了 = ✦ 上划一道斜线；指定的那个用不了 = 拔掉的插头（Unplug）。
 * 没有任何可用的 AI（`none`）不出图标，仍是居中的「连接 AI」胶囊（`DataDrawer`）。
 * 只选连接、不选型号；不写数据发去哪里的话。点选即关、图标立刻换成所选（等的时候 `aria-busy` 半透明）；失败回退并用胶囊说一句。
 */
export function DataAiChoice({ choice }: { choice: AiDataUnderstandingChoice }) {
  const { t } = useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<string | null>(null)
  const seq = useRef(0)

  /** 一个连接 id 的名字与图标：先看清单，再到能力缓存里找（指定的那个此刻用不了就不在清单里），都没有 = 取不到 */
  const info = useMemo(() => {
    return (id: string | null | undefined): { label: string; logo: string } | null => {
      if (!id) return null
      const o = choice.options.find((x) => x.id === id)
      if (o) return { label: o.label, logo: o.logo }
      const c = caps?.connections?.find((x) => x.id === id)
      return c ? { label: c.label, logo: c.logo } : null
    }
  }, [choice.options, caps])

  const shown = pending ?? valueOf(choice)
  // 图标：看「此刻显示的取值」（点选之后立刻换），不是还没回来的后端状态
  const kind: 'on' | 'off' | 'unavailable' = pending ? (pending === 'off' ? 'off' : 'on') : choice.state === 'off' ? 'off' : choice.state === 'unavailable' ? 'unavailable' : 'on'
  const conn = shown.startsWith(CONN) ? shown.slice(CONN.length) : choice.connection
  const cur = info(conn)
  const name = cur?.label ?? null

  const icon =
    kind === 'off' ? (
      <OffIcon />
    ) : kind === 'unavailable' ? (
      <Unplug size={ICON_SIZE.md} aria-hidden />
    ) : cur && hasBrandLogo(cur.logo) ? (
      <BrandLogo logo={cur.logo} size={16} />
    ) : (
      <Sparkles size={ICON_SIZE.md} aria-hidden />
    )
  const tip = kind === 'off' ? t('dataFigureV2.aiChoice.off') : kind === 'unavailable' ? t('dataFigureV2.aiChoice.unavailable') : name ? t('dataFigureV2.aiChoice.tip', { name }) : t('dataFigureV2.aiChoice.tipPlain')

  const choose = async (v: string) => {
    const mine = ++seq.current
    setPending(v)
    setOpen(false)
    try {
      await useDataFigureStore.getState().setUnderstandingChoice(v === 'auto' ? { mode: 'auto' } : v === 'off' ? { mode: 'off' } : { mode: 'connection', connection: v.slice(CONN.length) })
    } catch (e) {
      if (mine === seq.current) useUiStore.getState().setStatus(e instanceof ApiError ? backendErrorMsg(e) : msg('dataFigureV2.aiChoice.unavailable', undefined, 'ai'), 'info')
    } finally {
      if (mine === seq.current) setPending(null)
    }
  }

  const chosen = choice.mode === 'connection' ? choice.chosen : undefined
  const chosenInOptions = chosen ? choice.options.some((o) => o.id === chosen) : true
  // 指定的那个此刻用不了：它仍要在列表里看得出是它（可能已不在清单里），并写明现在没在用
  const missing = chosen && (choice.state === 'unavailable' || !chosenInOptions) ? (info(chosen) ?? { label: t('dataFigureV2.aiChoice.chosen'), logo: '' }) : null

  return (
    <Menu
      align="start"
      side="top"
      width={248}
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        // 打开时轻量重取一次：刚连上 / 刚断开的 AI 马上在列表里（只读、不联网）
        if (o) void useDataFigureStore.getState().refreshUnderstandingChoice()
      }}
      trigger={
        // 扁平的 ghost 图标钮（与「设置」和抽屉里的控件同一副皮）：没有底、没有投影，悬停 / 展开才有一层浅灰；气泡与可达名是同一句（`IconButton` 的 `label`）
        <IconButton
          label={tip}
          variant="ghost"
          data-data-ai-choice={kind}
          aria-busy={pending ? true : undefined}
          className={cn('pointer-events-auto', kind === 'on' ? 'text-ink-2' : 'text-ink-3', pending && 'opacity-60')}
        >
          {icon}
        </IconButton>
      }
    >
      <div data-data-ai-menu>
        <MenuRadioGroup value={shown} onValueChange={(v) => void choose(v)}>
          <MenuRadioItem
            value="auto"
            icon={Sparkles}
            // 自动且在用：第二行小字说明现在实际用的是谁（与 AI 行的型号小字同一个样式）
            description={choice.mode === 'auto' && choice.state === 'on' && info(choice.connection)?.label ? t('dataFigureV2.aiChoice.current', { name: info(choice.connection)!.label }) : undefined}
            data-data-ai-option="auto"
          >
            {t('dataFigureV2.aiChoice.auto')}
          </MenuRadioItem>
          {missing && chosen && (
            <MenuRadioItem value={`${CONN}${chosen}`} icon={missing.logo ? logoFor(missing.logo) : Unplug} reason={t('dataFigureV2.aiChoice.unavailable')} data-data-ai-option={`${CONN}${chosen}`}>
              {missing.label}
            </MenuRadioItem>
          )}
          {choice.options
            .filter((o) => !(missing && o.id === chosen))
            .map((o) => (
              <MenuRadioItem key={o.id} value={`${CONN}${o.id}`} icon={logoFor(o.logo)} description={o.model || undefined} data-data-ai-option={`${CONN}${o.id}`}>
                {o.label}
              </MenuRadioItem>
            ))}
          <MenuRadioItem value="off" icon={OffIcon} data-data-ai-option="off">
            {t('dataFigureV2.aiChoice.off')}
          </MenuRadioItem>
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuItem icon={Plus} data-data-ai-connect onSelect={() => requestConnectAi()}>
          {t('dataFigureV2.aiChoice.more')}
        </MenuItem>
      </div>
    </Menu>
  )
}
