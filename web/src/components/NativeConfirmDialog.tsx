import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LoaderCircle } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Badge } from './ui/Badge'
import { Checkbox } from './ui/Checkbox'
import { backendCodeMsg } from '@/lib/api'
import { t as translate, type UiMessage } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import { useNativeSessionStore, type NativeError } from '@/store/nativeSessionStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Notice } from './ui/Notice'

const nr = (key: string, values?: Record<string, unknown>) =>
  translate(`nativeRun.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 这几个码意味着**那份 descriptor 已经没了**（用过 / 过期 / 不认识），再点一次
 * 「运行并连接」只会拿到同一条错误。
 *
 * 它们会在批准失败之后出现，是因为后端在 attach **之前**就 `consume()` 了
 * descriptor（issue #190）：attach 被拒时凭据已经成了墓碑。那条归后端修；
 * 这边要做的是**别留一个点了也没用的按钮**——一个看起来可以重试、实际每次都
 * 给同一条错误的入口，比直接说"这条请求已经作废了"更劝退。
 */
const HANDOFF_GONE = new Set([
  'native_handoff_consumed',
  'native_handoff_expired',
  'native_handoff_invalid',
])

/**
 * `tavotto run` 的确认屏（ADR 0021 §7）。
 *
 * **这不是提示，是闸。** CLI 此刻正阻塞在「Waiting for Tavotto desktop…」上，
 * 而用户的 Python **一行都还没跑**——点下「运行并连接」之后 sidecar 才
 * attach，attach 成功才是 CLI「可以开跑了」的信号。所以：
 *
 * - **必须做出选择**（`blockDismiss`）：点外面和 Esc 都不算回答。随手关掉
 *   的表现是那个终端一直挂到 attach 超时，而用户以为自己只是关了个弹窗。
 * - **展示的是 descriptor 里的那条 invocation**，不是请求体里的任何东西：
 *   界面确认的是哪条命令，执行端就只能执行那条（TOCTOU 由后端看护，这边
 *   连能提交的字段都只有 `remember` 一个）。
 * - **`□ 记住此项目和此 Python` 默认不勾**。记住之后绑的是
 *   项目 × 解释器 × schema——解释器换了、项目搬了、schema 升了都会失效并
 *   重新问（§7.1）。即使记住，用户**仍然必须亲自敲那条命令**：这不是
 *   「允许 AI 自动执行」。
 */
export function NativeConfirmDialog() {
  useTranslation('dialogs')
  const fmt = useFormatMessage()
  const current = useNativeSessionStore((s) => s.pendingQueue[0] ?? null)
  const queued = useNativeSessionStore((s) => s.pendingQueue.length)
  const [remember, setRemember] = useState(false)
  // 常驻挂载、一个 lg 对话框从头到尾（2026-10-07 设计审计 §10.2）：载入 → 确认 → 失败只换正文，
  // 不在三种尺寸的两个 Dialog 之间交接；关的那 90ms 里队列已空，正文按最后一份载荷画
  const last = useRef(current)
  if (current) last.current = current
  const head = last.current

  // 换一条待确认的交接就把勾选还原：上一条勾没勾与这一条无关，而这个勾
  // 决定的是"以后不再问"——继承上一次的状态等于替用户做了决定。
  useEffect(() => setRemember(false), [head?.native_id])

  if (!head) return null
  const store = useNativeSessionStore.getState()
  const info = head.info
  const busy = head.submitting
  // descriptor 已经作废：这一屏没有"再试一次"，只有"关闭"
  const gone = !!head.error && HANDOFF_GONE.has(head.error.code)
  // 取不到（过期 / 已被处理 / ID 不对）：说清楚，并给一个能关掉的出口。
  // 转圈的对话框比一条错误更坏——它让人一直等一件不会发生的事。
  // 批准之后 descriptor 作废的那条走同一屏：两种情况下用户能做的事一模一样。
  const settled = !info || gone
  const dismiss = () => store.dismissPending(head.native_id)

  return (
    <Dialog
      open={!!current}
      // 闸：必须做出选择，点外面和 Esc 都不算回答（只有「已作废 / 取不到」那一屏没有待决的选择，能关）
      onOpenChange={(v) => !v && settled && dismiss()}
      blockDismiss={!settled}
      busy={settled ? head.loading : busy}
      onEscape={settled && !head.loading ? dismiss : undefined}
      title={nr('title')}
      // 「还没开始运行」那句说明按 2026-09-11 设计包去掉；排队里还有别的交接时
      // 仍要说出来——悄悄压着第二条等于让用户以为只有这一条
      description={!settled && queued > 1 ? nr('descriptionQueued', { queued: queued - 1 }) : undefined}
      size="lg"
      anchor="native-confirm"
      footer={
        settled
          ? {
              secondary: !head.loading && (
                <Button data-native-close variant="secondary" size="lg" onClick={dismiss}>
                  {translate('actions.close')}
                </Button>
              ),
            }
          : {
              secondary: (
                <Button
                  data-native-cancel
                  variant="secondary"
                  size="lg"
                  disabled={busy}
                  onClick={() => store.cancel(head.native_id)}
                >
                  {nr('cancel')}
                </Button>
              ),
              primary: (
                <Button
                  data-native-approve
                  variant="primary"
                  size="lg"
                  loading={busy}
                  loadingLabel={nr('approving')}
                  onClick={() => store.approve(head.native_id, remember)}
                >
                  {nr('approve')}
                </Button>
              ),
            }
      }
    >
      {settled ? (
        head.loading ? (
          <p data-native-loading className="flex items-center gap-2 text-ink-3">
            <LoaderCircle size={ICON_SIZE.sm} className="animate-spin" />
            <span className="text-shimmer">{nr('loading')}</span>
          </p>
        ) : (
          <ErrorNote error={head.error} fmt={fmt} />
        )
      ) : (
        <div className="flex flex-col gap-3">
          <dl className="flex flex-col gap-2">
            <Row label={nr('fields.target')}>
              <span className="font-mono text-sm text-ink" title={info.target_display}>
                {info.target_display}
              </span>
              <Badge className="ml-1.5">{nr(`targetKind.${info.target_kind}`)}</Badge>
            </Row>
            <Row label={nr('fields.interpreter')}>
              <span className="break-all font-mono text-sm text-ink" title={info.interpreter}>
                {info.interpreter}
              </span>
              {info.python_version && (
                <span className="ml-1.5 text-ink-3">
                  {nr('pythonVersion', { version: info.python_version })}
                </span>
              )}
            </Row>
            <Row label={nr('fields.cwd')}>
              <span className="break-all font-mono text-sm text-ink" title={info.cwd}>
                {info.cwd}
              </span>
            </Row>
            <Row label={nr('fields.project')}>
              <span className="break-all font-mono text-sm text-ink" title={info.project_root}>
                {info.project_root}
              </span>
            </Row>
            {info.arg_count > 0 && (
              /* **只有数量。** 参数的内容不经过界面（ADR 0021 §4：descriptor 里
                 本来就只记了个数），所以这里也说不出更多——不假装能。 */
              <Row label={nr('fields.args')}>
                <span className="text-ink-2">{nr('argCount', { count: info.arg_count })}</span>
              </Row>
            )}
          </dl>

          <label className="flex items-start gap-2 text-ink-2">
            <Checkbox
              checked={remember}
              disabled={busy}
              onChange={(e) => setRemember(e.target.checked)}
              className="mt-0.5"
            />
            <span className="min-w-0 flex-1">{nr('remember')}</span>
          </label>

          {/* 权限说明与错误：Notice，放在页脚上方（2026-10-07 设计审计 §10.2）。
              四句话是一条一条说的，不拼字符串——中英的从句位置不同，拼出来的句子读着就是机翻。 */}
          <Notice tone="warn" data-native-permission>
            {nr('permissionNotice')}
          </Notice>
          {head.error && <ErrorNote error={head.error} fmt={fmt} />}
        </div>
      )}
    </Dialog>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline gap-3">
      <dt className="w-24 shrink-0 text-sm text-ink-2">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  )
}

/** 后端的 code 在显示这一刻才翻（i18n 纪律：活得比一次渲染长的不存成品串）。 */
function ErrorNote({
  error,
  fmt,
}: {
  error: NativeError | null
  fmt: (m: UiMessage | null | undefined) => string
}) {
  if (!error) return null
  return <Notice tone="danger">{fmt(backendCodeMsg(error.code, error.params, error.message))}</Notice>
}
