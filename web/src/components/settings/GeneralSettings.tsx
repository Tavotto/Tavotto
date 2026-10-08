import { useTranslation } from 'react-i18next'
import { msg, setLocale, SUPPORTED_LOCALES, LOCALE_LABELS, t as translate } from '@/i18n'
import { useLocale } from '@/i18n/react'
import {
  resetHints,
  resetTutorial,
  runTutorialEntry,
  tutorialEntry,
  useTutorialStore,
} from '@/lib/onboarding/tutorial'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { Ellipsis, Lightbulb, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Kbd } from '../ui/Kbd'
import { Menu, MenuItem } from '../ui/Menu'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { CompanionDiagram } from './CompanionDiagram'
import { SettingRow, settingRowLabelId } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 通用（原「常规」+「界面」，2026-09-30 并页）。
 *
 * **四组**（2026-10-07 设计审计 §9.1）：语言与布局 / 侧栏 / 画布 / 学习。此前首个分区没有标题，五颗相同的
 * 次级按钮里三颗是教程的——「开始教程」「重置教程项目」「重新显示操作提示」各占一行。现在教程是一行
 * （主动作 + ⋯：重置教程项目、重新显示操作提示），快捷键速查表与它同组（都是「学怎么用」）。
 *
 * **「界面看起来不对？」当场生效**：此前只删掉本机存的那一份、提示「刷新页面后生效」，而刷新之前的任何一次
 * 偏好写入（拖一下侧栏宽度）又会把旧值写回去。现在是 `uiStore.resetLayoutPrefs()`。
 *
 * **固定侧栏在当前窗口不生效时停用开关、就近说原因**：窄窗口里侧栏是盖在画布上的抽屉，固定这件事根本不成立
 * ——此前开关照样能拨，拨了什么都不发生。中等宽度只是「同时只能固定一侧」，开关照常可用，现状一句话。
 * 像素断点不写在界面上（真实断点在 `uiStore` 是 WIDE=1280 / MEDIUM=1024）。
 *
 * **这一页只有一个问号**（拖动联动：figure 坐标、twinx 孪生轴这类实现词确实解释了「为什么偏偏是这几个对象」，
 * 但那是技术帮助，界面这一层交给示意图）。
 */
export function GeneralSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  return (
    <>
      <LanguageAndLayout />
      <Sidebars />
      <CanvasGroup close={close} />
      <Learning close={close} />
    </>
  )
}

function LanguageAndLayout() {
  const locale = useLocale()
  return (
    <FormSection title={st('general.groupLanguage')}>
      <FieldGroup>
        {/*
          语言：选完立刻生效（i18next 的 languageChanged 会让整棵树重渲染），
          偏好写在独立的 tavotto.locale 里，不进任何文档或项目数据。
        */}
        <SettingRow label={st('general.language')} data-settings-anchor="general.language">
          <Select
            className="w-full"
            ariaLabel={st('general.language')}
            value={locale}
            onChange={(v) => void setLocale(v as (typeof SUPPORTED_LOCALES)[number])}
            options={SUPPORTED_LOCALES.map((l) => ({ value: l, label: LOCALE_LABELS[l] }))}
          />
        </SettingRow>
        <SettingRow
          label={st('general.layout')}
          description={st('general.layoutDesc')}
          data-settings-anchor="general.layout"
        >
          <Button
            variant="secondary"
            size="sm"
            data-settings-reset-layout
            onClick={() => {
              useUiStore.getState().resetLayoutPrefs()
              useUiStore.getState().setStatus(msg('settings.general.layoutReset', undefined, 'dialogs'), 'done')
            }}
          >
            {st('general.resetLayout')}
          </Button>
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}

function Sidebars() {
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  const layout = useUiStore((s) => s.layout)
  // 'wide' 两侧都能固定，没有限制可说；medium 互斥（开关照常可用）、narrow 是覆盖层（固定不成立，开关停用）。
  // 状态取自布局状态本身，不在这里第二次判窗口宽度
  const narrow = layout === 'narrow'
  const pinLimit = narrow
    ? st('sidebars.pinLimitedNarrow')
    : layout === 'medium'
      ? st('sidebars.pinLimitedMedium')
      : undefined
  return (
    <FormSection title={st('general.groupSidebars')}>
      <FieldGroup>
        <SettingRow
          label={st('sidebars.leftPinned')}
          controlId="setting-left-pinned"
          status={pinLimit}
          data-settings-anchor="general.leftPinned"
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-left-pinned')}
            id="setting-left-pinned"
            checked={leftPinned}
            disabled={narrow}
            onChange={(v) => useUiStore.getState().setLeftPinned(v)}
          />
        </SettingRow>
        <SettingRow
          label={st('sidebars.rightPinned')}
          controlId="setting-right-pinned"
          status={pinLimit}
          data-settings-anchor="general.rightPinned"
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-right-pinned')}
            id="setting-right-pinned"
            checked={rightPinned}
            disabled={narrow}
            onChange={(v) => useUiStore.getState().setRightPinned(v)}
          />
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}

function CanvasGroup({ close }: { close: () => void }) {
  const withCompanions = useUiStore((s) => s.dragAxesWithCompanions)
  return (
    <FormSection title={st('general.groupCanvas')}>
      <FieldGroup>
        <SettingRow
          label={st('canvas.dragCompanions')}
          // 一行只留两种辅助（2026-09-14 审计 D2）：小问号里那句已经把色条轴 / 孪生轴说全了，
          // 说明行再说一遍是第二份；示意图讲空间关系，留着
          help={st('canvas.companionsExplain')}
          controlId="setting-drag-companions"
          // 示意图是说明的一部分，坐在标题列的说明下方（Session 6）
          illustration={<CompanionDiagram on={withCompanions} />}
          data-settings-anchor="general.dragCompanions"
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-drag-companions')}
            id="setting-drag-companions"
            checked={withCompanions}
            onChange={(v) => useUiStore.getState().setCanvasPref({ dragAxesWithCompanions: v })}
          />
        </SettingRow>
        <SettingRow label={st('canvas.more')} data-settings-anchor="general.canvas">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              close()
              useUiStore.getState().setRightTab('canvas')
            }}
          >
            {st('canvas.openCanvasSettings')}
          </Button>
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}

/**
 * 学习：新手教程一行（主动作 + ⋯）+ 快捷键速查表一行。
 *
 * 状态与动作都来自 `lib/onboarding/tutorial`——四个入口共用，这里不判状态。「重置教程项目」与「重新显示
 * 操作提示」是教程的低频动作，收进行尾 ⋯（审计 T38 当时把重置拆成单独一行，是为了不与主入口并排成两颗
 * 同权重的钮——收进菜单同样做到了这一点，而且不占一整行）。
 */
function Learning({ close }: { close: () => void }) {
  const status = useOnboardingStore((s) => s.status)
  const hasTutorial = useOnboardingStore((s) => s.tutorialProjectId != null)
  const busy = useTutorialStore((s) => s.busy)
  const entry = tutorialEntry(status)
  return (
    <FormSection title={st('general.groupLearning')}>
      <FieldGroup>
        <SettingRow label={st('tutorial.label')} data-settings-anchor="general.tutorial">
          <Button
            variant="secondary"
            size="sm"
            disabled={busy != null}
            data-onboarding-anchor="settings-tutorial"
            onClick={() => {
              // 先关设置：coachmark 要挂的目标都在工作台上，不在这个对话框里
              close()
              void runTutorialEntry('settings')
            }}
          >
            {st(`tutorial.${entry}`)}
          </Button>
          <Menu
            align="end"
            width={220}
            trigger={
              <IconButton label={st('tutorial.more')} iconSize="sm" data-settings-tutorial-more>
                <Ellipsis size={ICON_SIZE.sm} aria-hidden />
              </IconButton>
            }
          >
            {hasTutorial && (
              <MenuItem
                icon={RotateCcw}
                disabled={busy != null}
                onSelect={() => {
                  close()
                  void resetTutorial()
                }}
              >
                {st('tutorial.reset')}
              </MenuItem>
            )}
            <MenuItem icon={Lightbulb} onSelect={() => resetHints()}>
              {st('tutorial.resetHints')}
            </MenuItem>
          </Menu>
        </SettingRow>
        <SettingRow label={st('shortcuts.label')} data-settings-anchor="general.shortcuts">
          {/* 「按 ? 随时打开」原本是一段帮助文字。键位本身就是最短的说法——画成键帽
              （`Kbd`），键帽在**钮里**（全面打磨 D08） */}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              close()
              useUiStore.getState().setShortcutHelpOpen(true)
            }}
          >
            {st('shortcuts.open')}
            <Kbd>?</Kbd>
          </Button>
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}
