import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { AiAgentId, AiEndpoint, AiEndpointPreset, saveAiEndpoint } from '@/lib/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'
import { Select } from '../ui/Select'

/** 本节文案在 dialogs:settings.agents.* 下 */
const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const Row = ({
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
  <label className="flex min-h-7 flex-wrap items-center gap-2">
    <span className="flex w-24 shrink-0 items-baseline gap-0.5 text-xs text-ink-2">
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
    {children}
    {error && (
      <span role="alert" className="w-full text-xs text-danger">
        {error}
      </span>
    )}
  </label>
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
      <Row label={ag('endpoint.baseUrl')}>
        <TextInput
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          aria-label={ag('endpoint.baseUrl')}
          placeholder={wireApi ? 'https://…/v1' : 'https://…/anthropic'}
          className="flex-1 font-mono"
          spellCheck={false}
        />
      </Row>
      <Row label={ag('endpoint.models')}>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          {models.length > 0 && (
            <span className="flex flex-wrap gap-1" data-model-list>
              {models.map((m, i) => (
                <span
                  key={m}
                  className="flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs text-ink-2"
                >
                  {m}
                  {/* 第一个是默认值——这件事以前只写在占位文案里 */}
                  {i === 0 && (
                    <span className="text-[10px] text-ink-3">{ag('endpoint.modelDefault')}</span>
                  )}
                  <button
                    type="button"
                    aria-label={ag('endpoint.modelRemove', { name: m })}
                    onClick={() => setModels(models.filter((x) => x !== m))}
                    className="text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
                  >
                    <X size={10} aria-hidden />
                  </button>
                </span>
              ))}
            </span>
          )}
          <span className="flex items-center gap-1.5">
            <TextInput
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
              className="min-w-0 flex-1 font-mono"
              spellCheck={false}
            />
            <Button variant="outline" size="sm" disabled={!modelDraft.trim()} onClick={addModel}>
              {ag('endpoint.modelAdd')}
            </Button>
          </span>
        </span>
      </Row>
      {wireApi && (
        <Row label={ag('endpoint.wire')}>
          <Select
            value={wire}
            onChange={(v) => setWire(v as 'responses' | 'chat')}
            options={[
              { value: 'chat', label: ag('endpoint.wireChat') },
              { value: 'responses', label: ag('endpoint.wireResponses') },
            ]}
            ariaLabel={ag('endpoint.wireAria')}
            className="flex-1"
          />
        </Row>
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
          <Button variant="outline" size="md" onClick={onClose}>
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
        <div className="flex flex-col gap-2" data-endpoint-step="preset">
          <Row label={ag('endpoint.preset')} required>
            <Select
              value={preset}
              onChange={applyPreset}
              options={presets.map((p) => ({ value: p.id, label: p.label }))}
              placeholder={ag('endpoint.presetPlaceholder')}
              ariaLabel={ag('endpoint.presetAria')}
              className="flex-1"
            />
          </Row>
          <div>
            <Button variant="ghost" size="sm" onClick={() => setManual(true)}>
              {ag('endpoint.manual')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2" data-endpoint-step="form">
          <Row label={ag('endpoint.name')} required error={nameError}>
            <TextInput
              value={label}
              onChange={(e) => {
                setLabel(e.target.value)
                setNameError(null)
              }}
              aria-label={ag('endpoint.name')}
              aria-invalid={nameError ? true : undefined}
              className="flex-1"
            />
          </Row>
          <Row label={ag('endpoint.apiKey')}>
            <TextInput
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              aria-label={ag('endpoint.apiKey')}
              placeholder={
                existing?.has_key ? ag('endpoint.apiKeySaved', { hint: existing.key_hint }) : 'sk-…'
              }
              className="flex-1 font-mono"
              spellCheck={false}
            />
          </Row>

          {fromPreset ? (
            <details className="rounded-sm border border-border px-2 py-1.5">
              <summary className="cursor-default text-xs text-ink-2 outline-none focus-visible:focus-ring">
                {ag('endpoint.presetFilled')}
              </summary>
              <div className="mt-1.5 flex flex-col gap-2">{connectionFields}</div>
            </details>
          ) : (
            connectionFields
          )}

          {/* 隐私说明：一句准确的短话，细节展开。**不许虚构安全保证**——
              下面每一句都对着 `engine/ai_providers.py` 与 `engine/config.py`
              核过（审计 T45）。特别是权限那条：`_harden()` 在 Windows 上
              直接 return，所以那句话不能说成"已经收好了"。 */}
          <p className="text-xs leading-relaxed text-ink-3">{ag('endpoint.keyNote')}</p>
          <details className="text-xs leading-relaxed text-ink-3">
            <summary className="cursor-default outline-none focus-visible:focus-ring">
              {ag('endpoint.keyNoteMore')}
            </summary>
            <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
              <li>{ag('endpoint.keyNoteWhere')}</li>
              <li>{ag('endpoint.keyNotePerms')}</li>
              <li>{ag('endpoint.keyNoteDiagnostics')}</li>
            </ul>
          </details>
        </div>
      )}
    </Dialog>
  )
}
