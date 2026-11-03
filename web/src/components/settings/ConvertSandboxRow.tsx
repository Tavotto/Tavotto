import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { setConvertUnsandboxedWindows } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { FieldGroup } from '../ui/FormSection'
import { Toggle } from '../ui/Toggle'
import { ag } from './agentState'
import { SettingRow, settingRowLabelId } from './SettingRow'

/**
 * 设置 → 改图助手 → 「高级」里的一个开关：「转换时允许 AI 不受沙盒限制」（TVD-BCZ0 方案 B，默认关）。
 *
 * 只在 Windows 后端出现：后端只在 Windows 上给 `convert_unsandboxed_windows` 这一键，缺省（别的平台 / 老后端）= 整个不渲染。
 * 打开后 Windows 上 R / MATLAB / Python 的**转换**会话里 Codex 以 `danger-full-access` 运行；其它任务与平台不变（后端判定）。
 * **界面上只有这一行**：不加说明、副标题、警告横条（用户硬性要求）。
 */
export function ConvertSandboxRow() {
  useTranslation('dialogs')
  const flag = useAiStore((s) => s.caps?.convert_unsandboxed_windows)
  const [value, setValue] = useState(flag === true)
  const [busy, setBusy] = useState(false)
  useEffect(() => setValue(flag === true), [flag])
  if (typeof flag !== 'boolean') return null

  const change = async (next: boolean) => {
    const prev = value
    setValue(next) // 先动：开关不该等一次本机往返
    setBusy(true)
    try {
      const r = await setConvertUnsandboxedWindows(next)
      setValue(r.convert_unsandboxed_windows)
    } catch {
      setValue(prev) // 没存上就退回原样，不让界面说谎（用户可再点一次）
      setBusy(false)
      return
    }
    // 已存上：能力刷新失败不退回（后端已是新值），保留 PUT 返回的值
    await useAiStore.getState().loadCaps(false).catch(() => undefined)
    setBusy(false)
  }

  return (
    <FieldGroup data-convert-sandbox-group>
      <SettingRow
        label={ag('convertUnsandboxed')}
        controlId="setting-convert-unsandboxed"
        data-settings-anchor="ai.convertUnsandboxed"
        data-convert-unsandboxed
      >
        <Toggle
          aria-labelledby={settingRowLabelId('setting-convert-unsandboxed')}
          id="setting-convert-unsandboxed"
          checked={value}
          disabled={busy}
          onChange={(v) => void change(v)}
        />
      </SettingRow>
    </FieldGroup>
  )
}
