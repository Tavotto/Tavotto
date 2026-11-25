import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { AgentIcon } from '@/components/settings/AgentIcon'
import { VendorIcon } from '@/components/settings/VendorIcon'
import { Check, ChevronDown, ChevronRight, Download, Plus } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import { Radio } from '@/components/ui/Radio'
import { cn } from '@/lib/utils'
import { Menu, MenuItem, MenuRadioGroup, MenuRadioItem, MenuSeparator, MenuSub } from '@/components/ui/Menu'
import { BrandLogo } from '@/components/settings/BrandLogo'
import { t as translate } from '@/i18n'
import { connectionAgent, type AiAgentCaps, type AiConnection } from '@/lib/api'
import { connectionInfo, modelDetail, useAiStore } from '@/store/aiStore'
import { requestConnectAi } from '@/store/connectAiStore'
import { manualHow, startCliUpdate, updatableInApp, useCliUpdateError, useCliUpdateRunning } from './cliUpdate'

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

/** 连接的图标：彩色 logo 坐在浅色圆角底上；菜单项与触发器各用一个稳定的组件类型（避免每次渲染重建） */
const logoTypes = new Map<string, () => ReactElement>()
export const logoFor = (logo: string) => {
  let c = logoTypes.get(logo)
  if (!c) {
    c = () => <BrandLogo logo={logo} size={16} />
    logoTypes.set(logo, c)
  }
  return c
}

/** 一个连接下可选的型号 id；当前值（记忆或默认）不在清单里时照实放在最前，不静默换掉 */
function connectionModels(c: AiConnection, current: string | null): string[] {
  const list = [...c.models]
  if (current && !list.includes(current)) list.unshift(current)
  return list
}

/**
 * 型号子菜单的末行：背后那份 CLI 比型号清单旧（`cli_update`）时，新型号可能用不了——点一下更新；
 * 点不了（只能自己更新）时置灰、第二行说怎么做。没这回事就什么都不画。
 */
function CliUpdateItem({ conn }: { conn: AiConnection }) {
  const agent = connectionAgent(conn)
  const running = useCliUpdateRunning(agent)
  const failed = useCliUpdateError(agent)
  const name = useAiStore((s) => s.caps?.agents.find((a) => a.id === agent)?.display_name) ?? agent
  const u = conn.cli_update
  if (!u || !agent) return null
  const inApp = updatableInApp(u)
  return (
    <>
      <MenuSeparator />
      <MenuItem
        icon={Download}
        data-ai-cli-update={u.action}
        disabled={running || !inApp}
        reason={running ? undefined : failed ? failed : inApp ? ai('cliUpdate.menuHint') : manualHow(u)}
        onSelect={() => startCliUpdate(agent)}
      >
        {ai(running ? 'cliUpdate.running' : inApp ? 'cliUpdate.action' : 'cliUpdate.menuManual', { agent: name })}
      </MenuItem>
    </>
  )
}

/**
 * 「连接 AI」视图下的两级选择器：一级 = 已连接的 AI（彩色 logo + 厂商名），二级 = 该连接的型号；
 * 底部「＋ 连接更多 AI…」打开连接小弹窗。**只是呈现**：选中一项写 aiStore 的 `connection` 与
 * `models[连接 id]`（与旧选择器同一套记忆机制，切回另一个连接时它自己的型号记忆还在）。
 * 型号清单为空 = 跟随默认：这一家没有第二级，选中即切换连接，绝不伪造型号。
 */
export function ConnectionModelPicker({
  connections,
  active,
  model,
}: {
  connections: AiConnection[]
  active: string
  model: string
}) {
  useTranslation('ai')
  const cur = connections.find((c) => c.id === active)
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
          aria-label={ai('panel.aiModel')}
          className="focus-visible:focus-ring flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border bg-surface px-2 text-left text-xs text-ink outline-none hover:bg-surface-hover data-[state=open]:bg-surface-hover"
        >
          <BrandLogo logo={cur.logo} size={16} />
          <span className="min-w-0 flex-1 truncate">
            {model ? `${cur.label} · ${modelDetail(connectionInfo(cur), model)?.display_name || model}` : cur.label}
          </span>
          <ChevronDown size={ICON_SIZE.xs} aria-hidden className="shrink-0 opacity-70" />
        </button>
      }
    >
      <div data-ai-model-menu="connections">
        {connections.map((c) => {
          const isActive = c.id === active
          const Icon = logoFor(c.logo)
          if (c.models.length === 0 && !(isActive && model)) {
            return (
              <MenuItem
                key={c.id}
                icon={Icon}
                data-ai-connection={c.id}
                onSelect={() => useAiStore.getState().setConnection(c.id)}
              >
                {c.label}
              </MenuItem>
            )
          }
          return (
            <MenuSub key={c.id} label={c.label} icon={Icon} width={264} data-ai-connection={c.id}>
              <MenuRadioGroup
                data-ai-model-menu={c.id}
                value={isActive ? model : ''}
                onValueChange={(m) => {
                  useAiStore.getState().setConnection(c.id)
                  useAiStore.getState().setModel(c.id, m)
                }}
              >
                {connectionModels(c, isActive ? model : null).map((m) => (
                  <MenuRadioItem
                    key={m}
                    value={m}
                    data-ai-model-item={m}
                    tag={m === c.default_model ? ai('panel.effortDefault') : undefined}
                  >
                    {modelDetail(connectionInfo(c), m)?.display_name || m}
                  </MenuRadioItem>
                ))}
              </MenuRadioGroup>
              <CliUpdateItem conn={c} />
            </MenuSub>
          )
        })}
      </div>
      <MenuSeparator />
      <MenuItem icon={Plus} data-ai-connect-more onSelect={() => requestConnectAi()}>
        {ai('panel.connectMore')}
      </MenuItem>
    </Menu>
  )
}

/** 订阅类连接（ChatGPT 登录 / CLI 官方登录）：名字后面灰字标「订阅」，与同一家的 API Key 连接分得开 */
const isSubscription = (c: AiConnection) => c.id === 'siwc:chatgpt' || c.id.startsWith('sub:')

const ROW = 'focus-visible:focus-ring flex w-full min-w-0 items-center gap-3 rounded-md px-1.5 py-1.5 text-left text-sm outline-none hover:bg-surface-hover'

/** 型号列表末行（同 `CliUpdateItem` 的判据，换成列表行的样子） */
function CliUpdateRow({ conn }: { conn: AiConnection }) {
  const agent = connectionAgent(conn)
  const running = useCliUpdateRunning(agent)
  const failed = useCliUpdateError(agent)
  const name = useAiStore((s) => s.caps?.agents.find((a) => a.id === agent)?.display_name) ?? agent
  const u = conn.cli_update
  if (!u || !agent) return null
  const inApp = updatableInApp(u)
  const reason = running ? undefined : failed ? failed : inApp ? ai('cliUpdate.menuHint') : manualHow(u)
  return (
    <button
      type="button"
      data-ai-cli-update={u.action}
      disabled={running || !inApp}
      onClick={() => startCliUpdate(agent)}
      className={cn(ROW, 'pl-9 disabled:cursor-default disabled:hover:bg-transparent')}
    >
      <Download size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-ink-2" />
      <span className="flex min-w-0 flex-col">
        <span className={cn('truncate', !inApp || running ? 'text-ink-3' : 'text-ink')}>
          {ai(running ? 'cliUpdate.running' : inApp ? 'cliUpdate.action' : 'cliUpdate.menuManual', { agent: name })}
        </span>
        {reason && <span className="type-caption">{reason}</span>}
      </span>
    </button>
  )
}

/**
 * 「连接 AI」视图下「AI 与模型」的**一个列表**（2026-11-25 用户：与设置里的连接列表同一种样子，不再「下拉里套菜单」）：
 * 每个已连接的 AI 一行（logo 在 24px 一列居中、名字同一列，订阅类后面灰字「订阅」）；点一行就在它下面展开型号（当前用的那个默认展开），
 * 选中的型号右侧打勾，默认型号后面灰字「默认」；没有型号清单的连接点一下即切换。末行「＋ 连接更多 AI…」。
 * **只是呈现**：选中写 aiStore 的 `connection` 与 `models[连接 id]`，与 `ConnectionModelPicker` 同一套。
 */
export function ConnectionModelList({
  connections,
  active,
  model,
}: {
  connections: AiConnection[]
  active: string
  model: string
}) {
  useTranslation('ai')
  const [open, setOpen] = useState<string | null>(active || null)
  return (
    <div data-ai-model-menu="connections" className="-mx-1.5 flex max-h-80 flex-col overflow-y-auto">
      {connections.map((c) => {
        const isActive = c.id === active
        const models = connectionModels(c, isActive ? model : null)
        const expandable = models.length > 0
        const expanded = expandable && open === c.id
        return (
          <div key={c.id} className="flex flex-col">
            <button
              type="button"
              data-ai-connection={c.id}
              aria-expanded={expandable ? expanded : undefined}
              aria-current={isActive && !expandable ? 'true' : undefined}
              onClick={() => (expandable ? setOpen(expanded ? null : c.id) : useAiStore.getState().setConnection(c.id))}
              className={ROW}
            >
              <IconSlot className="w-6">
                <BrandLogo logo={c.logo} size={16} />
              </IconSlot>
              <span className="min-w-0 flex-1 truncate text-ink">
                {c.label}
                {isSubscription(c) && <span className="ml-1.5 text-xs text-ink-3">{ai('panel.kindSubscription')}</span>}
              </span>
              {expandable ? (
                <ChevronRight size={ICON_SIZE.xs} aria-hidden className={cn('shrink-0 text-ink-3 transition-transform', expanded && 'rotate-90')} />
              ) : (
                isActive && <Check size={ICON_SIZE.sm} aria-hidden className="shrink-0 text-accent" />
              )}
            </button>
            {expanded && (
              // 型号行比 AI 行矮一档、小一号，组后留一点空：一眼看出是上一行的下一级（agy 审稿）
              <div data-ai-model-menu={c.id} className="flex flex-col pb-1">
                {models.map((m) => {
                  const on = isActive && m === model
                  return (
                    // 公共单选 `ui/Radio`：同一连接的型号同一个 name，方向键在组内走、整组只占一个 Tab 停靠点；
                    // data-ai-model-item 在 label 上（整行可点、文字在里面），input 带 data-ai-model-radio
                    <label key={m} data-ai-model-item={m} className={cn(ROW, 'cursor-pointer py-1 text-[13px]')}>
                      <IconSlot className="w-6">
                        <Radio
                          name={`model-${c.id}`}
                          value={m}
                          checked={on}
                          data-ai-model-radio=""
                          onChange={() => {
                            useAiStore.getState().setConnection(c.id)
                            useAiStore.getState().setModel(c.id, m)
                          }}
                        />
                      </IconSlot>
                      <span className={cn('min-w-0 flex-1 truncate', on ? 'text-ink' : 'text-ink-2')}>
                        {modelDetail(connectionInfo(c), m)?.display_name || m}
                        {m === c.default_model && <span className="ml-1.5 text-xs text-ink-3">{ai('panel.effortDefault')}</span>}
                      </span>
                    </label>
                  )
                })}
                <CliUpdateRow conn={c} />
              </div>
            )}
          </div>
        )
      })}
      <div aria-hidden className="mx-1.5 my-1 border-t border-border" />
      <button type="button" data-ai-connect-more onClick={() => requestConnectAi()} className={ROW}>
        <IconSlot className="w-6 text-ink-2">
          <Plus size={ICON_SIZE.sm} aria-hidden />
        </IconSlot>
        <span className="min-w-0 flex-1 truncate text-ink-2">{ai('panel.connectMore')}</span>
      </button>
    </div>
  )
}
