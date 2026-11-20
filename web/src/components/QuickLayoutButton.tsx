import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, LayoutGrid } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  QUICK_LAYOUT_GAPS,
  QUICK_LAYOUT_TARGETS,
  readQuickLayoutPrefs,
  writeQuickLayoutPrefs,
  type QuickLayoutPrefs,
  type QuickLayoutTarget,
} from '@/lib/quickLayoutPrefs'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { runQuickLayout, useQuickLayoutStore } from '@/store/quickLayoutActions'
import { PremiumLock } from './license/PremiumLock'
import { Button } from './ui/Button'
import { Menu, MenuCheckItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator } from './ui/Menu'
import { Tip } from './ui/Tooltip'

/**
 * 画布工具条上的「一键排版」（ADR 0199，高级功能 `quick-layout`）：一枚带字的 ghost 钮 + 贴着它的小下拉
 * （目标宽度 单栏 / 双栏 / 整页，子图间距 2 / 3 / 4 mm；选择记在本机）。
 *
 * 授权入口没出现（`licenseVisible` 为假：上线开关关着、老后端）= **整个不画**；出现了但没解锁 = `PremiumLock`
 * 置灰 + 悬停一句话（不弹窗）。这里的判断只决定画法，真正的权益判断在后端（ADR 0199 §九）。
 */
export function QuickLayoutButton({
  label,
  noFocusOnPointer,
}: {
  /** 窄画布下收成只有图标的那层包装（与工具条里别的动作钮同一个） */
  label: (text: string) => React.ReactNode
  noFocusOnPointer: (e: React.MouseEvent) => void
}) {
  const { t } = useTranslation('workspace')
  const status = useLicenseStore((s) => s.status)
  const load = useLicenseStore((s) => s.load)
  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  const busy = useQuickLayoutStore((s) => s.busy)
  const [prefs, setPrefs] = useState<QuickLayoutPrefs>(readQuickLayoutPrefs)
  if (!licenseVisible(status)) return null

  const update = (next: QuickLayoutPrefs) => {
    setPrefs(next)
    writeQuickLayoutPrefs(next)
  }

  return (
    <PremiumLock feature="quick-layout">
      <span data-quick-layout className="inline-flex items-center">
        <Tip label={t('topbar.quickLayoutTip')}>
          <Button
            size="md"
            data-toolbar-item
            data-quick-layout-run
            loading={busy}
            disabled={busy}
            onMouseDown={noFocusOnPointer}
            onClick={() => void runQuickLayout(prefs.target, prefs.gapMm)}
            aria-label={t('topbar.quickLayout')}
            className="rounded-r-none pr-2"
          >
            {!busy && <LayoutGrid size={ICON_SIZE.sm} />}
            {label(t('canvasTools.quickLayout'))}
          </Button>
        </Tip>
        <Menu
          width={224}
          align="center"
          pointerKeepsFocus
          trigger={
            <Button
              size="md"
              data-toolbar-item
              data-quick-layout-options
              onMouseDown={noFocusOnPointer}
              aria-label={t('topbar.quickLayoutOptions')}
              className="w-auto rounded-l-none px-1 text-ink-3 hover:text-ink"
            >
              <ChevronDown size={ICON_SIZE.xs} />
            </Button>
          }
        >
          <MenuLabel>{t('quickLayout.targetLabel')}</MenuLabel>
          <MenuRadioGroup
            value={prefs.target}
            onValueChange={(v) => update({ ...prefs, target: v as QuickLayoutTarget })}
          >
            {QUICK_LAYOUT_TARGETS.map((target) => (
              <MenuRadioItem key={target} value={target} data-quick-layout-target={target}>
                {t(`quickLayout.target.${target}`)}
              </MenuRadioItem>
            ))}
          </MenuRadioGroup>
          <MenuSeparator />
          <MenuLabel>{t('quickLayout.gapLabel')}</MenuLabel>
          <MenuRadioGroup
            value={String(prefs.gapMm)}
            onValueChange={(v) => update({ ...prefs, gapMm: Number(v) })}
          >
            {QUICK_LAYOUT_GAPS.map((gap) => (
              <MenuRadioItem key={gap} value={String(gap)} data-quick-layout-gap={gap}>
                {t('quickLayout.gap', { mm: gap })}
              </MenuRadioItem>
            ))}
          </MenuRadioGroup>
          <MenuSeparator />
          <MenuCheckItem
            checked={prefs.fixSpec}
            onSelect={() => update({ ...prefs, fixSpec: !prefs.fixSpec })}
            data-quick-layout-fix-spec
          >
            {t('quickLayout.fixSpec')}
          </MenuCheckItem>
        </Menu>
      </span>
    </PremiumLock>
  )
}
