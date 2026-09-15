import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, X } from 'lucide-react'
import { Details, Summary } from '../ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { AiAgentId, AiEndpoint, AiEndpointPreset, saveAiEndpoint } from '@/lib/api'
import { cn } from '@/lib/utils'
import { FormRow } from '../FormRow'
import { Button } from '../ui/Button'
import { FIELD_BOX, FIELD_FOCUS_WITHIN } from '../ui/fieldBox'
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'
import { Select } from '../ui/Select'

/** 本节文案在 dialogs:settings.agents.* 下 */
const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 一行字段：**标签列在左 80px、控件在右**（全面打磨 D22，L1）——与导出对话框、
 * 论文样式对话框同一副 `FormRow`。此前这里标签在上、控件通栏，是全站唯一一处
 * 竖排表单，而它就开在设置窗口里。整行仍是 `<label>`：这几个控件都是 `<input>`，
 * 点标签文字等于点输入框。
 */
const Field = ({
  label,
  required,
  error,
  children,
}: {
  label: string
  /** 必填的那一个标上星号，并把「必填」念给读屏听 */
  required?: boolean
  error?: string | null
  children: React.ReactNode
}) => (
  <FormRow
    asLabel
    align="start"
    label={
      <span className="flex items-baseline gap-0.5">
        {label}
        {required && (
          <>
            <span aria-hidden className="text-danger">
              *
            </span>
            <span className="sr-only">{ag('endpoint.requiredAria')}</span>
          </>
        )}
      </span>
    }
  >
    <span className="flex min-w-0 flex-1 flex-col gap-1">
      {children}
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </span>
  </FormRow>
)

/**
 * 第三方接口编辑。
 *
 * 只从 **Agent 详情 → 模型服务** 打开，不出现在一级设置页——Base URL、
 * 密钥、wire api 是少数人用一次的技术细节，摆在首屏只会让「什么都不用配」
 * 这句话失去说服力。
 *
 * 密钥只写不读：后端从不回传，留空即保留原值，所以编辑一个已有接口时
 * 不必重新粘贴密钥。
 *
 * **两步，不是一张大表**（审计 T45）：新建时先选一个服务预设，地址 / 模型 /
 * 协议由它填好，第二步只剩「名称 + 密钥」；预设填好的那三项折在下面，要改
 * 才展开。没有合适的预设时可以「手动填写」，那时字段一起摊开——那才是真需要
 * 逐个填的场景。编辑一个已存在的接口同样直接进第二步。
 *
 * 模型是**一串条目**而不是一行逗号分隔的字符串：分隔符是什么、空格算不算、
 * 哪个是默认，用户从一个输入框里看不出来。
 *
 * 必填只有名称一项——这是**后端的判据**（`app.py` 保存 endpoint 时只挡
 * `name_missing`），不是这里自己定的；地址留空的语义是「用这个 CLI 自己的
 * 登录态」，不是"没填完"。
 */
export function EndpointDialog({
  agent,
  agentLabel,
  wireApi,
  existing,
  presets,
  onClose,
  onSave,
}: {
  agent: AiAgentId
  agentLabel: string
  /** 该 Agent 是否需要选择 wire api（OpenAI 兼容那一族才有） */
  wireApi: boolean
  existing: AiEndpoint | null
  presets: AiEndpointPreset[]
  onClose: () => void
  onSave: (rec: Parameters<typeof saveAiEndpoint>[0]) => void
}) {
  const { t } = useTranslation(['dialogs', 'common'])
  const [label, setLabel] = useState(existing?.label ?? '')
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState<string[]>(existing?.models ?? [])
  const [modelDraft, setModelDraft] = useState('')
  const [wire, setWire] = useState<'responses' | 'chat'>(existing?.wire_api ?? 'chat')
  const [nameError, setNameError] = useState<string | null>(null)

  // 选中的预设留在本地态：`ui/Select` 是受控的，不留住这一格触发器会一直显示
  // 占位文案，用户看不出自己刚选了哪一个（原生 `<select>` 靠浏览器自己记）
  const [preset, setPreset] = useState('')

  /**
   * 还停在「选预设」那一步吗。编辑已有接口、或这个 Agent 一条预设都没有时，
   * 这一步本来就不存在——那时直接进表单，不摆一个只有占位文案的下拉框。
   */
  const [manual, setManual] = useState(false)
  const choosing = !existing && presets.length > 0 && !preset && !manual
  /** 预设填好的那三项（地址 / 模型 / 协议）默认折起来；手动填写时它们就是主体 */
  const fromPreset = !!preset

  const applyPreset = (id: string) => {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    setPreset(id)
    setLabel(p.label)
    setBaseUrl(p.base_url)
    setModels([...p.models])
    if (p.wire_api) setWire(p.wire_api)
  }

  const addModel = () => {
    const v = modelDraft.trim()
    if (!v || models.includes(v)) {
      setModelDraft('')
      return
    }
    setModels([...models, v])
    setModelDraft('')
  }

  const submit = () => {
    const name = label.trim()
    if (!name) {
      // 指出**具体是哪一格**，而不是把保存按钮灰掉让用户自己找
      setNameError(ag('endpoint.nameRequired'))
      return
    }
    onSave({
      id: existing?.id,
      label: name,
      agent,
      base_url: baseUrl.trim(),
      api_key: apiKey.trim() || undefined,
      models,
      wire_api: wire,
    })
  }

  const connectionFields = (
    <>
      <Field label={ag('endpoint.baseUrl')}>
        <TextInput
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          aria-label={ag('endpoint.baseUrl')}
          placeholder={wireApi ? 'https://…/v1' : 'https://…/anthropic'}
          className="w-full font-mono"
          spellCheck={false}
        />
      </Field>
      <Field label={ag('endpoint.models')}>
        {/* 一个框装下全部：条目是 chip，输入紧跟在最后一个 chip 之后，回车即添加 */}
        {/* 框走全站唯一那一副（全面打磨 D22，§5）：此前它自己描了一圈 18% 的
            `border-strong`，比同一屏里别的可编辑框重一档 */}
        <span
          className={cn(
            FIELD_BOX,
            FIELD_FOCUS_WITHIN,
            'flex min-h-10 w-full flex-wrap items-center gap-1.5 px-2 py-1.5',
          )}
          data-model-list
        >
          {models.map((m, i) => (
            /* chip 是状态标签的形状，不是一颗 28 高的小按钮（全面打磨 D22）：20 高、
               无边、surface-2 底——与 `Badge` 同一种读法（这里不能直接用 `Badge`：
               里面还挂着一颗删除钮，而 Badge 是纯文字的） */
            <span
              key={m}
              className="flex h-5 max-w-full items-center gap-1 rounded-sm bg-surface-2 pl-1.5 pr-0.5 font-mono text-xs text-ink-2"
            >
              {m}
              {/* 第一个是默认值——这件事以前只写在占位文案里 */}
              {i === 0 && (
                <span className="font-sans text-xs text-ink-3">
                  {ag('endpoint.modelDefault')}
                </span>
              )}
              <button
                type="button"
                aria-label={ag('endpoint.modelRemove', { name: m })}
                onClick={() => setModels(models.filter((x) => x !== m))}
                className="grid size-4 place-items-center rounded-sm text-ink-faint outline-none hover:bg-border hover:text-ink-2 focus-visible:focus-ring"
              >
                <X size={ICON_SIZE.xs} aria-hidden />
              </button>
            </span>
          ))}
          <input
            value={modelDraft}
            onChange={(e) => setModelDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return
              // 回车加一条，**不提交对话框**——否则打一半就存下去了
              e.preventDefault()
              addModel()
            }}
            placeholder={ag('endpoint.modelPlaceholder')}
            aria-label={ag('endpoint.modelDraftAria')}
            className="h-7 min-w-[150px] flex-1 bg-transparent px-1 font-mono text-sm text-ink outline-none placeholder:text-ink-faint"
            spellCheck={false}
          />
          {modelDraft.trim() && (
            <Button variant="ghost" size="sm" onClick={addModel}>
              {ag('endpoint.modelAdd')}
            </Button>
          )}
        </span>
      </Field>
      {wireApi && (
        <Field label={ag('endpoint.wire')}>
          <Select
            value={wire}
            onChange={(v) => setWire(v as 'responses' | 'chat')}
            options={[
              { value: 'chat', label: ag('endpoint.wireChat') },
              { value: 'responses', label: ag('endpoint.wireResponses') },
            ]}
            ariaLabel={ag('endpoint.wireAria')}
            className="w-full"
          />
        </Field>
      )}
    </>
  )

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={
        existing
          ? ag('endpoint.editTitle', { label: existing.label })
          : ag('endpoint.addTitle', { name: agentLabel })
      }
      size="md"
      footer={
        <>
          <Button variant="ghost" size="md" onClick={onClose}>
            {t('common:actions.cancel')}
          </Button>
          {!choosing && (
            <Button variant="primary" size="md" onClick={submit}>
              {t('common:actions.save')}
            </Button>
          )}
        </>
      }
    >
      {choosing ? (
        /* 第一步：选一个预设。地址 / 模型 / 协议由它填好，第二步只剩名称 + 密钥 */
        <div className="flex flex-col gap-4" data-endpoint-step="preset">
          <Field label={ag('endpoint.preset')} required>
            <Select
              value={preset}
              onChange={applyPreset}
              options={presets.map((p) => ({ value: p.id, label: p.label }))}
              placeholder={ag('endpoint.presetPlaceholder')}
              ariaLabel={ag('endpoint.presetAria')}
              className="w-full"
            />
          </Field>
          <div>
            <Button variant="ghost" size="sm" onClick={() => setManual(true)}>
              {ag('endpoint.manual')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-5" data-endpoint-step="form">
          <Field label={ag('endpoint.name')} required error={nameError}>
            <TextInput
              value={label}
              onChange={(e) => {
                setLabel(e.target.value)
                setNameError(null)
              }}
              aria-label={ag('endpoint.name')}
              aria-invalid={nameError ? true : undefined}
              className="w-full"
            />
          </Field>
          <div className="flex flex-col gap-1.5">
            <Field label={ag('endpoint.apiKey')}>
              <TextInput
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                aria-label={ag('endpoint.apiKey')}
                placeholder={
                  existing?.has_key
                    ? ag('endpoint.apiKeySaved', { hint: existing.key_hint })
                    : 'sk-…'
                }
                className="w-full font-mono"
                spellCheck={false}
              />
            </Field>
            {/* 隐私说明：一句准确的短话紧跟在密钥下面，细节在末尾展开。**不许虚构安全保证**——
                下面每一句都对着 `engine/ai_providers.py` 与 `engine/config.py`
                核过（审计 T45）。特别是权限那条：`_harden()` 在 Windows 上
                直接 return，所以那句话不能说成"已经收好了"。 */}
          </div>

          {fromPreset ? (
            <Details className="group border-y border-border">
              {/* 折叠头与别处同高（全面打磨 D22，§3：一律 28），不是 48 */}
              <Summary className="flex min-h-7 cursor-default items-center gap-2 text-sm font-medium text-ink-2">
                <span className="min-w-0 flex-1 truncate">{ag('endpoint.presetFilled')}</span>
                <span className="shrink-0 truncate text-xs font-normal text-ink-3">
                  {presets.find((p) => p.id === preset)?.label}
                </span>
                <ChevronRight
                  size={ICON_SIZE.sm}
                  className="shrink-0 text-ink-3 transition-transform group-open:rotate-90"
                  aria-hidden
                />
              </Summary>
              <div className="flex flex-col gap-5 pb-5 pt-1">{connectionFields}</div>
            </Details>
          ) : (
            <div className="flex flex-col gap-5 border-t border-border pt-5">{connectionFields}</div>
          )}

          <Details className="group text-xs leading-relaxed text-ink-3">
            <Summary className="inline-flex cursor-default items-center gap-1 hover:text-ink-2">
              <ChevronRight
                size={ICON_SIZE.xs}
                className="shrink-0 transition-transform group-open:rotate-90"
                aria-hidden
              />
              {ag('endpoint.keyNoteMore')}
            </Summary>
            {/* 密钥那一句原本常驻在密钥框下面（全面打磨 D22）：它和这里的三条是同一件事，
                而那一句又是三条里的第一条——常驻一份、展开再读一遍 */}
            <ul className="mt-2 flex list-disc flex-col gap-1.5 pl-5">
              <li>{ag('endpoint.keyNote')}</li>
              <li>{ag('endpoint.keyNoteWhere')}</li>
              <li>{ag('endpoint.keyNotePerms')}</li>
              <li>{ag('endpoint.keyNoteDiagnostics')}</li>
            </ul>
          </Details>
        </div>
      )}
    </Dialog>
  )
}
