import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { AgentIcon } from '@/components/settings/AgentIcon'
import { VendorIcon } from '@/components/settings/VendorIcon'
import { ChevronDown } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Menu, MenuItem, MenuRadioGroup, MenuRadioItem, MenuSub } from '@/components/ui/Menu'
import { t as translate } from '@/i18n'
import type { AiAgentCaps } from '@/lib/api'
import { modelDetail, useAiStore } from '@/store/aiStore'

/**
 * 「执行器与模型」两级选择器（beta 0.19 反馈）。
 *
 * 第一级是**执行器家族**（Claude Code / Codex …，带各自图标），第二级才是该家族的模型型号
 * （只写显示名，不带说明）。弹出与键盘全部由 Radix 菜单负责：→ / Enter 进子菜单，
 * ← / Esc 收回一层，↑↓ 在层内移动；子菜单项是 `menuitemradio`，当前型号 aria-checked。
 *
 * **只是呈现**：选中一项仍写 aiStore 的两个字段（agent / models[agent]）。外层的两个入口
 * （助手面板里的「执行器与模型」行、输入框下方的胶囊弹层）渲染的都是这一个组件。
 */
const ai = (key: string, opts?: Record<string, unknown>) => translate(key, { ns: 'ai', ...opts })

/**
 * 一个执行器的图标。当它**当前走第三方接口**时换成该厂商的图标（接口行不挤、不叠角标）：执行器名仍写在
 * 旁边的文字里（「Claude Code · deepseek-v4-pro」），所以图标只回答「现在接的是谁」，不丢「是哪个执行器」。
 * `vendorKey` 为 `undefined` = 用 CLI 自己的登录态，显示执行器图标。
 */
export const FamilyIcon = ({ iconKey, vendorKey }: { iconKey: string; vendorKey?: string }) =>
  vendorKey ? (
    <VendorIcon iconKey={vendorKey} size={ICON_SIZE.sm} glyphSize={ICON_SIZE.sm} />
  ) : (
    <AgentIcon iconKey={iconKey} size={ICON_SIZE.sm} glyphSize={ICON_SIZE.sm} />
  )

/** 这个执行器当前接管它的第三方接口的图标键；没有接管（官方登录态）→ undefined */
const vendorKeyOf = (a: AiAgentCaps) => (a.endpoint ? (a.endpoint.icon_key ?? 'custom') : undefined)

/** `MenuItem` / `MenuSub` 要的是组件类型：按 key 缓存，别每次渲染造一个新类型（会让图标反复卸载） */
const iconTypes = new Map<string, () => ReactElement>()
const iconFor = (iconKey: string, vendorKey?: string) => {
  const k = `${iconKey}|${vendorKey ?? ''}`
  let c = iconTypes.get(k)
  if (!c) {
    c = () => <FamilyIcon iconKey={iconKey} vendorKey={vendorKey} />
    iconTypes.set(k, c)
  }
  return c
}

/** 型号显示名：后端给了 display_name 用它，否则（旧后端 / 第三方接口）就是 id 原文 */
export function modelLabel(info: AiAgentCaps | null | undefined, model: string): string {
  return modelDetail(info, model)?.display_name || model
}

/** 一个家族下可选的型号 id；当前值（记忆或 CLI 默认）不在清单里时照实放在最前，不静默换掉 */
function familyModels(a: AiAgentCaps, current: string | null): string[] {
  const list = [...a.models]
  if (current && !list.includes(current)) list.unshift(current)
  return list
}

export function ModelPicker({
  usable,
  active,
  model,
}: {
  usable: AiAgentCaps[]
  active: string
  model: string
}) {
  useTranslation('ai')
  const cur = usable.find((a) => a.id === active)
  if (!cur) return null
  return (
    <Menu
      align="start"
      side="bottom"
      width={248}
      trigger={
        <button
          type="button"
          data-ai-model-picker="trigger"
          aria-label={ai('panel.agentModel')}
          className="focus-visible:focus-ring flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border bg-surface px-2 text-left text-xs text-ink outline-none hover:bg-surface-hover data-[state=open]:bg-surface-hover"
        >
          <FamilyIcon iconKey={cur.icon_key} vendorKey={vendorKeyOf(cur)} />
          <span className="min-w-0 flex-1 truncate">
            {model ? `${cur.display_name} · ${modelLabel(cur, model)}` : cur.display_name}
          </span>
          <ChevronDown size={ICON_SIZE.xs} aria-hidden className="shrink-0 opacity-70" />
        </button>
      }
    >
      <div data-ai-model-menu="families">
        {usable.map((a) => {
          const isActive = a.id === active
          const Icon = iconFor(a.icon_key, vendorKeyOf(a))
          // 清单为空 = 跟随 CLI 默认：这一家没有第二级，选中即切换执行器，绝不伪造型号
          if (a.models.length === 0 && !(isActive && model)) {
            return (
              <MenuItem
                key={a.id}
                icon={Icon}
                data-ai-family={a.id}
                onSelect={() => useAiStore.getState().setAgent(a.id)}
              >
                {a.display_name}
              </MenuItem>
            )
          }
          return (
            <MenuSub key={a.id} label={a.display_name} icon={Icon} width={264} data-ai-family={a.id}>
              <MenuRadioGroup
                data-ai-model-menu={a.id}
                value={isActive ? model : ''}
                onValueChange={(m) => {
                  useAiStore.getState().setAgent(a.id)
                  useAiStore.getState().setModel(a.id, m)
                }}
              >
                {familyModels(a, isActive ? model : null).map((m) => {
                  // 只写型号名（beta 0.19.0b3 反馈）：不再带说明 / id 副行；说明字段仍由后端给，只是不显示
                  const name = modelDetail(a, m)?.display_name || m
                  return (
                    <MenuRadioItem
                      key={m}
                      value={m}
                      data-ai-model-item={m}
                      tag={m === a.default_model ? ai('panel.effortDefault') : undefined}
                    >
                      {name}
                    </MenuRadioItem>
                  )
                })}
              </MenuRadioGroup>
            </MenuSub>
          )
        })}
      </div>
    </Menu>
  )
}
