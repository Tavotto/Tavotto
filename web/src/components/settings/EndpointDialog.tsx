import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { AiAgentId, AiEndpoint, AiEndpointPreset, saveAiEndpoint } from '@/lib/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'

/** 本节文案在 dialogs:settings.agents.* 下 */
const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <label className="flex min-h-7 items-center gap-2">
    <span className="w-24 shrink-0 text-xs text-ink-2">{label}</span>
    {children}
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
  const [models, setModels] = useState((existing?.models ?? []).join(', '))
  const [wire, setWire] = useState<'responses' | 'chat'>(existing?.wire_api ?? 'chat')

  const applyPreset = (id: string) => {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    setLabel(p.label)
    setBaseUrl(p.base_url)
    setModels(p.models.join(', '))
    if (p.wire_api) setWire(p.wire_api)
  }

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
          <Button
            variant="primary"
            size="md"
            disabled={!label.trim()}
            onClick={() =>
              onSave({
                id: existing?.id,
                label: label.trim(),
                agent,
                base_url: baseUrl.trim(),
                api_key: apiKey.trim() || undefined,
                models: models
                  .split(/[,，\s]+/)
                  .map((s) => s.trim())
                  .filter(Boolean),
                wire_api: wire,
              })
            }
          >
            {t('common:actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {!existing && presets.length > 0 && (
          <Row label={ag('endpoint.preset')}>
            <select
              defaultValue=""
              onChange={(e) => applyPreset(e.target.value)}
              aria-label={ag('endpoint.presetAria')}
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="">{ag('endpoint.presetPlaceholder')}</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </Row>
        )}
        <Row label={ag('endpoint.name')}>
          <TextInput value={label} onChange={(e) => setLabel(e.target.value)} className="flex-1" />
        </Row>
        <Row label={ag('endpoint.baseUrl')}>
          <TextInput
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={wireApi ? 'https://…/v1' : 'https://…/anthropic'}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label={ag('endpoint.apiKey')}>
          <TextInput
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              existing?.has_key ? ag('endpoint.apiKeySaved', { hint: existing.key_hint }) : 'sk-…'
            }
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label={ag('endpoint.models')}>
          <TextInput
            value={models}
            onChange={(e) => setModels(e.target.value)}
            placeholder={ag('endpoint.modelsPlaceholder')}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        {wireApi && (
          <Row label={ag('endpoint.wire')}>
            <select
              value={wire}
              onChange={(e) => setWire(e.target.value as 'responses' | 'chat')}
              aria-label={ag('endpoint.wireAria')}
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="chat">{ag('endpoint.wireChat')}</option>
              <option value="responses">{ag('endpoint.wireResponses')}</option>
            </select>
          </Row>
        )}
        <p className="text-xs leading-relaxed text-ink-3">{ag('endpoint.keyNote')}</p>
      </div>
    </Dialog>
  )
}
