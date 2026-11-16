import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, backendErrorText, connectAi, type AiConnectResult, type AiEndpoint, type saveAiEndpoint } from '@/lib/api'
import { probeReasonText } from '@/lib/connectionCheck'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EndpointDialog } from '../EndpointDialog'
import { ag, cx } from './copy'

/** 保存失败的一句话：几个常见的拒绝原因给具体说法，其余走通用翻译 */
function saveErrorText(e: unknown): string {
  if (e instanceof ApiError && e.body.code === 'endpoint_invalid') {
    const reason = (e.body.params as { reason?: string } | undefined)?.reason
    if (reason === 'api_key_invalid') return cx('keyMissing')
    if (reason === 'key_origin_mismatch') return ag('endpoint.fetchReason.key_origin_mismatch')
    if (reason === 'insecure_url') return ag('endpoint.fetchReason.insecure_url')
  }
  return backendErrorText(e)
}

/** 探测不通的原因 → 贴在哪一格、写什么；地址类的原因悬停补一句常见原因 */
function noteOf(reason: string): { field: 'key' | 'model' | 'url'; text: string; title?: string } {
  if (reason === 'unauthorized' || reason === 'quota') return { field: 'key', text: probeReasonText(reason) }
  if (reason === 'no_model') return { field: 'model', text: probeReasonText(reason) }
  return { field: 'url', text: probeReasonText(reason), title: cx('probeHint') }
}

/**
 * 保存后这条连接能不能用 → null（能用）或失败原因。以对话探测的结论为准：型号清单取不到（很多中转站没有 /models）
 * 会让连接视图暂时是 error，但只要探测通了就是能用；探测没拿到结论（断网 / 缺型号）才回头看连接状态。
 */
function failureReason(res: AiConnectResult): string | null {
  const probe = res.probe
  if (probe?.checked) return probe.reason
  const conn = res.connection
  if (!conn || conn.state === 'ready') return null
  return conn.error_reason ?? conn.error ?? probe?.reason ?? 'network'
}

/**
 * 「其他兼容接口…」：直接走现有 `EndpointDialog` 的手动模式（没有预设可选 = 直接是地址 / 密钥 / 型号表单）。
 * 保存走 `POST /api/ai/connections`（不传 `wire` = 保存时自动探测对话协议，界面没有协议选项），存好重读 capabilities 再通知调用方。
 * 保存了但探测不通：表单里写一句原因、表单不关（再点保存是改同一条，不会再新建一条）。
 *
 * 两种失败分开说：**保存失败**——表单里写一句原因，用户改了再存；**保存成功但刷新失败**——记住「已经存了」，
 * 换成「已保存，没能刷新」+「重试刷新」，重试只重读、绝不再发一次（无 id 的）新建。
 */
export function CompatibleEndpoint({
  onClose,
  onSaved,
  existing = null,
  focus,
}: {
  onClose: () => void
  onSaved: () => void
  /** 编辑一条已存的连接（失败卡「检查连接」）：表单预填它的地址 / 型号，保存带它的 id；不给 = 新建 */
  existing?: AiEndpoint | null
  /** 打开后先聚焦哪一格（失败卡 / 连接卡的「重填密钥」「改模型」「改地址」） */
  focus?: 'key' | 'model' | 'url'
}) {
  useTranslation('dialogs')
  const [saveError, setSaveError] = useState<string | null>(null)
  // 存下来了但探测不通：原因贴在对应的输入框下面（不进底栏）
  const [note, setNote] = useState<{ field: 'key' | 'model' | 'url'; text: string; title?: string } | null>(null)
  const [saved, setSaved] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const busy = useRef(false)

  const refresh = async (): Promise<boolean> => {
    try {
      await useAiStore.getState().loadCaps(true)
      return true
    } catch {
      return false
    }
  }

  /** 这个表单已经存下来的那条连接 id：之后再点保存是改它，不是再新建 */
  const savedId = useRef<string | null>(null)

  const save = async (rec: Parameters<typeof saveAiEndpoint>[0]) => {
    if (busy.current || saved) return // 已经存过了：任何路径都不再发新建
    busy.current = true
    setSaveError(null)
    setNote(null)
    const id = savedId.current ?? rec.id
    let res: AiConnectResult
    try {
      res = await connectAi({
        // 改已存的连接：不给厂商，后端按这条记录自己的方式（如 dashscope-coding:key）处理——发父厂商会被当成换了厂商（vendor_mismatch）
        ...(id ? { id } : { vendor: 'custom' }),
        api_key: rec.api_key,
        base_url: rec.base_url,
        model: rec.models?.[0],
        // 全部型号一并发（后端去重、留下用户自己填的）；编辑时也不丢其余型号
        ...(rec.models && rec.models.length > 0 ? { models: rec.models } : {}),
        label: rec.label,
      })
    } catch (e) {
      setSaveError(saveErrorText(e))
      busy.current = false
      return
    }
    if (res.connection?.id) savedId.current = res.connection.id
    const ok = await refresh()
    busy.current = false
    if (!ok) {
      setSaved(true)
      return
    }
    const why = failureReason(res)
    if (why === null) onSaved()
    else setNote(noteOf(why))
  }

  if (saved) {
    return (
      <Dialog
        open
        onOpenChange={(v) => !v && onClose()}
        title={cx('other')}
        size="sm"
        footer={{
          secondary: (
            <Button variant="secondary" size="lg" onClick={onClose}>
              {cx('later')}
            </Button>
          ),
          primary: (
            <Button
              variant="primary"
              size="lg"
              data-endpoint-retry-refresh
              loading={refreshing}
              onClick={async () => {
                setRefreshing(true)
                const ok = await refresh()
                setRefreshing(false)
                if (ok) onSaved()
              }}
            >
              {cx('retryRefresh')}
            </Button>
          ),
        }}
      >
        <p data-endpoint-saved-notice className="text-base text-ink-2">
          {cx('savedRefreshFailed')}
        </p>
      </Dialog>
    )
  }

  return (
    <EndpointDialog
      agent={existing?.agent ?? 'codex'}
      agentLabel=""
      wireApi
      existing={existing}
      presets={[]}
      saveError={saveError}
      focus={focus}
      fieldNote={note}
      onClose={onClose}
      onSave={(rec) => void save(rec)}
    />
  )
}
