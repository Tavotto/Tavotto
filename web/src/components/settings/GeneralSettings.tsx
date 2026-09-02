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
import { Button } from '../ui/Button'
import { Select } from '../ui/Select'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 常规。保留的是「语言」「恢复默认布局」「快捷键速查表」三个真动作；
 * 「什么时候生效」「自动保存怎么实现」「重置具体影响什么」三段解释进问号。
 *
 * 快捷键那一行从独立分区并进来（ADR 0038）：一个分区只有一个按钮，是导航
 * 上的噪音；它本身不是设置，是一个入口。
 */
export function GeneralSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  const locale = useLocale()
  return (
    <SettingSection>
      {/*
        语言：选完立刻生效（i18next 的 languageChanged 会让整棵树重渲染），
        偏好写在独立的 tavotto.locale 里，不进任何文档或项目数据。
      */}
      <SettingRow label={st('general.language')} help={st('general.languageHint')}>
        <Select
          className="w-[160px]"
          ariaLabel={st('general.language')}
          value={locale}
          onChange={(v) => void setLocale(v as (typeof SUPPORTED_LOCALES)[number])}
          options={SUPPORTED_LOCALES.map((l) => ({ value: l, label: LOCALE_LABELS[l] }))}
        />
      </SettingRow>
      <SettingRow label={st('general.autosave')} help={st('general.autosaveHint')}>
        <span className="text-xs text-ink-3">{st('general.autosaveState')}</span>
      </SettingRow>
      <SettingRow label={st('general.layout')} help={st('general.resetLayoutHint')}>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            try {
              localStorage.removeItem('tavotto.ui')
            } catch {
              /* 忽略 */
            }
            setStatus(msg('settings.general.layoutReset', undefined, 'dialogs'))
          }}
        >
          {st('general.resetLayout')}
        </Button>
      </SettingRow>
      <SettingRow label={st('shortcuts.label')} help={st('shortcuts.hint')}>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setShortcutHelpOpen(true)
          }}
        >
          {st('shortcuts.open')}
        </Button>
      </SettingRow>
      <TutorialRows close={close} />
    </SettingSection>
  )
}

/**
 * 新手教程两行：状态 + 开始 / 继续 / 重新开始；重置提示。
 * 状态与动作都来自 `lib/onboarding/tutorial`——四个入口共用，这里不判状态。
 */
function TutorialRows({ close }: { close: () => void }) {
  const status = useOnboardingStore((s) => s.status)
  const hasTutorial = useOnboardingStore((s) => s.tutorialProjectId != null)
  const busy = useTutorialStore((s) => s.busy)
  const entry = tutorialEntry(status)
  return (
    <>
      <SettingRow
        label={st('tutorial.label')}
        help={st('tutorial.hint')}
        status={st(`tutorial.state.${status}`)}
      >
        <Button
          variant="outline"
          size="sm"
          disabled={busy != null}
          data-onboarding-anchor="settings-tutorial"
          onClick={() => {
            // 先关设置：coachmark 要挂的目标都在工作台上，不在这个对话框里
            close()
            void runTutorialEntry()
          }}
        >
          {st(`tutorial.${entry}`)}
        </Button>
        {hasTutorial && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy != null}
            onClick={() => {
              close()
              void resetTutorial()
            }}
          >
            {st('tutorial.reset')}
          </Button>
        )}
      </SettingRow>
      <SettingRow label={st('tutorial.hints')} help={st('tutorial.hintsHint')}>
        <Button variant="outline" size="sm" onClick={() => resetHints()}>
          {st('tutorial.resetHints')}
        </Button>
      </SettingRow>
    </>
  )
}
