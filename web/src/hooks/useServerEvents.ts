import { useEffect } from 'react'
import { msg, t } from '@/i18n'
import { subscribeEvents, type ServerEvent } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'

const short = (id: string) => id.split('/').pop()?.replace(/\.[^.]+$/, '') ?? id
const stemOf = (fileId: string) => short(fileId)

/** 冷启动耗时提示；light 没有提示（本来就快） */
const costHint = (cost: string): string =>
  cost === 'heavy' || cost === 'medium'
    ? t(`status.coldHint.${cost}`, { ns: 'workspace' })
    : ''

/** 单条事件的处理；抽成具名函数，免得 subscribeEvents 的两个参数挤成一坨 */
function handleEvent(ev: ServerEvent) {
  const setStatus = useUiStore.getState().setStatus
  const render = useRenderStore.getState()

  // SSE 是全进程共享的一条流，后端同时端着多个项目。带了 pj 的事件只属于
  // 那个项目——本标签页开的是另一个图库时必须无视它，否则会拿别人的脚本
  // 变更把自己的面板判成过期、白跑一轮 heavy 重建。
  const mine = useProjectStore.getState().project?.id
  if ('pj' in ev && ev.pj && mine && ev.pj !== mine) return

  switch (ev.kind) {
    case 'engine.bootstrap':
      // 渲染环境安装进度（建 venv + 装 matplotlib）
      useEnvStore.getState().onProgress(ev)
      break

    case 'render.started': {
      // 事件只带 fileId，而渲染态按变体分键：冷启动提示记在**文件级**的
      // building 表里，不写进任何一个变体条目——同文件另一个副本被盖成
      // 「渲染中」之后没人会来收掉它（它自己根本没在渲染）。
      render.noteBuilding(ev.id, { cold: !!ev.cold, cost: ev.cost ?? '' })
      if (ev.cold) {
        const hint = costHint(ev.cost ?? '')
        setStatus(
          msg(
            hint ? 'status.buildingWithHint' : 'status.building',
            { name: short(ev.id), hint },
            'workspace',
          ),
        )
      }
      break
    }
    case 'render.done':
      render.noteBuilding(ev.id, null)
      setStatus(msg('status.renderDone', { name: short(ev.id) }, 'workspace'))
      break
    case 'render.failed':
      render.noteBuilding(ev.id, null)
      setStatus(
        msg(
          ev.error ? 'status.renderFailedWithError' : 'status.renderFailed',
          { name: short(ev.id), error: ev.error ?? '' },
          'workspace',
        ),
        'error',
      )
      break

    case 'panel.file_changed': {
      const stems = new Set(ev.stems ?? [])
      // stems 是脚本产出的面板名，映射回文档里用到的文件 id。
      // runtime 面板按持久化描述块的 stem 认领（id 是不透明标识，不反解）
      const affected = useDocumentStore
        .getState()
        .doc.objects.filter(
          (o) =>
            o.type === 'panel' &&
            (o.fileKind === 'runtime'
              ? o.source != null && stems.has(o.source.stem)
              : stems.has(stemOf(o.fileId))),
        )
        .map((o) => (o as { fileId: string }).fileId)
      // 转入引擎跟踪 → useEngineSync 立刻按当前 overrides 冷重建，
      // 用户不需要再进编辑态就能在画布上看到新脚本的效果。
      // runtime 面板：本会话跑过的与文件面板同一待遇（热重建）；只在
      // 重开文档、还没跑过的那些上 lazy 纪律才生效（renderTargets 的门）。
      // stale 判定一并作废，下次查询按新脚本重新判
      render.markStale([...new Set(affected)])
      useRuntimeAssetStore.getState().invalidate([...new Set(affected)])
      useAssetStore.getState().load()
      if (affected.length) {
        setStatus(msg('status.scriptChanged', { count: affected.length }, 'workspace'))
      }
      break
    }

    case 'probe.started':
      // 「运行并发现图」的执行确认：starting_runtime → running
      useScriptRunStore.getState().markRunning(ev.script)
      break

    case 'registry.changed': {
      // 注册表变了（本标签页 probe 成功 / 另一标签页登记 / 手工裁决）：
      // 脚本清单与 runtime 素材清单都要重取——但只重取**已经取过的**，
      // 没打开过素材面板的标签页不必为别人的登记发请求
      const lib = useScriptLibraryStore.getState()
      if (lib.loaded) void lib.load()
      const runtime = useRuntimeAssetStore.getState()
      if (runtime.assets !== null) void runtime.loadAssets()
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
        msg(
          ev.status === 'done'
            ? ev.changed
              ? 'status.aiChanged'
              : 'status.aiNoChange'
            : ev.status === 'timeout'
              ? 'status.aiTimeout'
              : 'status.aiFailed',
          undefined,
          'ai',
        ),
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
