import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { setChangePlanEnabled } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { FieldGroup } from '../ui/FormSection'
import { Toggle } from '../ui/Toggle'
import { ag } from './agentState'
import { SettingRow, settingRowLabelId } from './SettingRow'

/**
 * 设置 → 改图助手 → 「高级」里的一个开关：「改图前先给我看修改方案」（ADR 0206 PR 4，默认开）。
 * 关掉 = 改图助手直接改脚本、一键排版直接落地（旧行为）。后端不给 `change_plan_enabled`（老后端 / 还没探测到）= 整行不渲染。
 * 界面上只有这一行：不加说明、副标题、警告横条。
 */
export function ChangePlanRow() {
  useTranslation('dialogs')
  const flag = useAiStore((s) => s.caps?.change_plan_enabled)
  const [value, setValue] = useState(flag !== false)
  const [busy, setBusy] = useState(false)
  useEffect(() => setValue(flag !== false), [flag])
  if (typeof flag !== 'boolean') return null

  const change = async (next: boolean) => {
    const prev = value
    setValue(next) // 先动：开关不该等一次本机往返
    setBusy(true)
    try {
      const r = await setChangePlanEnabled(next)
      setValue(r.change_plan_enabled)
    } catch {
      setValue(prev) // 没存上就退回原样，不让界面说谎
      setBusy(false)
      return
    }
    // 已存上：能力刷新失败不退回（后端已是新值）
    await useAiStore.getState().loadCaps(false).catch(() => undefined)
    setBusy(false)
  }

  return (
    <FieldGroup data-change-plan-group>
      <SettingRow
        label={ag('changePlan')}
        controlId="setting-change-plan"
        data-settings-anchor="ai.changePlan"
        data-change-plan-switch
      >
        <Toggle
          aria-labelledby={settingRowLabelId('setting-change-plan')}
          id="setting-change-plan"
          checked={value}
          disabled={busy}
          onChange={(v) => void change(v)}
        />
      </SettingRow>
    </FieldGroup>
  )
}
