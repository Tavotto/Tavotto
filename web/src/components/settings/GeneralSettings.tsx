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
 * 常规。五个真动作：语言、恢复默认布局、快捷键、教程、重新显示操作提示。
 *
 * **这一页一个问号都没有**（UI 审计「说明文字专项补查」）。改动前每一行标签
 * 后面都挂着一个点击式问号，而打开之后是：
 *   * 「界面语言」→「只影响界面文字…」——标题已经说了「界面」，这是同一句话；
 *   * 「快捷键速查表」→「全部快捷键见速查表（按 ? 随时打开）」——用一个帮助
 *     弹层去介绍另一个帮助入口，那个 ? 直接摆出来就行；
 *   * 「新手教程」→「用一份离线的示例项目走一遍…」——教程讲什么该由教程的
 *     起始页讲，这里的任务是让人进得去；
 *   * 「情境提示」→「第一次遇到某类操作时出现的一次性提示」——它定义「一次性
 *     提示是什么」，而用户要的是「点了会怎样」，所以按钮改叫「重新显示操作提示」。
 * 剩下真正有价值的那点（重置动作影响哪些东西、自动保存与命名副本的分工）改成
 * 标签下面的一行短说明，读一行就够，不用先点开什么。
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
      <SettingRow label={st('general.language')}>
        <Select
          className="w-[160px]"
          ariaLabel={st('general.language')}
          value={locale}
          onChange={(v) => void setLocale(v as (typeof SUPPORTED_LOCALES)[number])}
          options={SUPPORTED_LOCALES.map((l) => ({ value: l, label: LOCALE_LABELS[l] }))}
        />
      </SettingRow>
      {/* 自动保存没有开关可调，它是一句现状。目录与写盘时机进帮助文档；
          这里只留「不用手动保存」与「命名副本走哪儿」这两件当下用得上的事 */}
      <SettingRow label={st('general.autosave')} description={st('general.autosaveNamedCopy')}>
        <span className="text-xs text-ink-3">{st('general.autosaveState')}</span>
      </SettingRow>
      <SettingRow label={st('general.layout')} description={st('general.resetLayoutScope')}>
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
      <SettingRow label={st('shortcuts.label')}>
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
        {/* 「按 ? 随时打开」原本是一段帮助文字。键位本身就是最短的说法 */}
        <kbd className="rounded-sm border border-border px-1 font-mono text-[11px] leading-5 text-ink-3">
          ?
        </kbd>
      </SettingRow>
      <TutorialRows close={close} />
    </SettingSection>
  )
}

/**
 * 教程三行：进入教程（状态在右）、重置示例项目、重新显示操作提示。
 *
 * 状态与动作都来自 `lib/onboarding/tutorial`——四个入口共用，这里不判状态。
 * **重置单独一行**（审计 T38 / 说明文字第 5 条）：改动前它是主入口旁边的一个
 * 幽灵按钮，「再看一遍教程」与它的区别得点开问号才知道；现在它自己一行，
 * 标签下面直接写清重置的是哪个对象、代价是什么。
 */
function TutorialRows({ close }: { close: () => void }) {
  const status = useOnboardingStore((s) => s.status)
  const hasTutorial = useOnboardingStore((s) => s.tutorialProjectId != null)
  const busy = useTutorialStore((s) => s.busy)
  const entry = tutorialEntry(status)
  return (
    <>
      <SettingRow label={st('tutorial.label')} status={st(`tutorial.state.${status}`)}>
        <Button
          variant="outline"
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
      </SettingRow>
      {hasTutorial && (
        <SettingRow label={st('tutorial.reset')} description={st('tutorial.resetScope')}>
          <Button
            variant="outline"
            size="sm"
            disabled={busy != null}
            onClick={() => {
              close()
              void resetTutorial()
            }}
          >
            {st('tutorial.resetAction')}
          </Button>
        </SettingRow>
      )}
      <SettingRow label={st('tutorial.hints')}>
        <Button variant="outline" size="sm" onClick={() => resetHints()}>
          {st('tutorial.resetHints')}
        </Button>
      </SettingRow>
    </>
  )
}
