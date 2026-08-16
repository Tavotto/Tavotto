import { useEffect } from 'react'
import { subscribeEvents, type ServerEvent } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

const short = (id: string) => id.split('/').pop()?.replace(/\.[^.]+$/, '') ?? id
const stemOf = (fileId: string) => short(fileId)

const COST_HINT: Record<string, string> = {
  heavy: '冷启动可能需要几分钟',
  medium: '冷启动约十几秒',
  light: '',
}

/** 单条事件的处理；抽成具名函数，免得 subscribeEvents 的两个参数挤成一坨 */
function handleEvent(ev: ServerEvent) {
  const setStatus = useUiStore.getState().setStatus
  const render = useRenderStore.getState()

  switch (ev.kind) {
    case 'engine.bootstrap':
      // 渲染环境安装进度（建 venv + 装 matplotlib）
      useEnvStore.getState().onProgress(ev)
      break

    case 'render.started': {
      render.patch(ev.id, { status: 'rendering', cold: !!ev.cold, cost: ev.cost ?? '' })
      if (ev.cold) {
        const hint = COST_HINT[ev.cost ?? ''] ?? ''
        setStatus(`正在构建 ${short(ev.id)}${hint ? `（${hint}）` : '…'}`)
      }
      break
    }
    case 'render.done':
      setStatus(`渲染完成：${short(ev.id)}`)
      break
    case 'render.failed':
      setStatus(`渲染失败：${short(ev.id)}${ev.error ? ` — ${ev.error}` : ''}`, 'error')
      break

    case 'panel.file_changed': {
      const stems = new Set(ev.stems ?? [])
      // stems 是脚本产出的面板名，映射回文档里用到的文件 id
      const affected = useDocumentStore
        .getState()
        .doc.objects.filter((o) => o.type === 'panel' && stems.has(stemOf(o.fileId)))
        .map((o) => (o as { fileId: string }).fileId)
      // 转入引擎跟踪 → useEngineSync 立刻按当前 overrides 冷重建，
      // 用户不需要再进编辑态就能在画布上看到新脚本的效果
      render.markStale([...new Set(affected)])
      useAssetStore.getState().load()
      if (affected.length) {
        setStatus(`脚本已更新，面板已按新脚本重渲染（${affected.length} 个）`)
      }
      break
    }

    case 'ai.delta':
      useAiStore.getState().appendDelta(ev.session, ev.kindOf ?? 'message', ev.text)
      break

    case 'ai.done': {
      const ai = useAiStore.getState()
      ai.finish(ev)
      const sess = ai.sessions.find((s) => s.id === ev.session)
      if (ev.changed && sess?.fileId) {
        // 不等 watcher：立刻把目标面板转入引擎跟踪并重建。
        // 同脚本的其它面板由随后的 panel.file_changed 覆盖。
        render.markStale([sess.fileId])
      }
      setStatus(
        ev.status === 'done'
          ? ev.changed
            ? 'AI 已修改脚本，正在重建图表…'
            : 'AI 运行完成，但脚本没有变化'
          : `AI 任务${ev.status === 'timeout' ? '超时' : '失败'}`,
        ev.status === 'done' ? 'info' : 'error',
      )
      break
    }
  }
}

/** 后端事件 → 渲染状态 / AI 会话 / 素材库刷新 / 状态栏 */
export function useServerEvents() {
  useEffect(
    () =>
      subscribeEvents(handleEvent, () =>
        // 后端重启后 SSE 会重连，借这个时机让版本自检立刻复查一次
        window.dispatchEvent(new Event('mm:sse-open')),
      ),
    [],
  )
}
