import { useEffect } from 'react'
import { i18n, msg, t } from '@/i18n'
import {
  affectedAssetIdsOf,
  affectedStemsOf,
  subscribeEvents,
  type ServerEvent,
} from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { applyExportJob } from '@/store/exportStore'
import { recoverAfterReconnect, refreshAssetsAndSync } from '@/store/liveSync'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
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

/**
 * 单条事件的处理。**导出是为了让用例驱动同一份判断**——与 `useEngineSync`
 * 导出 `syncEngine` 同一条纪律：经 `EventSource` 去测的话，测的是 jsdom 的
 * SSE 实现，而这里要钉的是「收到这条事件之后 store 变成什么样」。
 */
export function handleServerEvent(ev: ServerEvent) {
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

    case 'export.progress':
      // 导出作业的进度与终局（ADR 0031）。SSE 是**加速器不是唯一通道**：
      // exportStore 自己还有一条轮询，两条路进的是同一个 applyExportJob()
      applyExportJob(ev)
      break

    case 'engine.dependency':
      // 受控依赖修复的进度（ADR 0019）。**不带 pj**：它是按 plan_id 走的，
      // 而 plan 本身绑定了项目——多开标签页时各自只认自己那条计划。
      useDepRepairStore.getState().onProgress(ev)
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
      const stems = new Set(affectedStemsOf(ev))
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
      // 重建**不等**素材刷新：脚本变了而它产出的 PDF 还没重新生成时，
      // /api/panels 里的 mtime 一动不动，等它等不来。派生元数据的同步照常
      // 跟在刷新后面（走合并入口，与同一批里的其它事件共用一个请求）。
      void refreshAssetsAndSync()
      if (affected.length) {
        setStatus(msg('status.scriptChanged', { count: affected.length }, 'workspace'))
      }
      break
    }

    case 'assets.changed': {
      // 素材本身变了（脚本重新产出了 PDF / 用户在外面换了张图 / 删了一张）。
      // 刷新之后：`mtime` 换代 → 静态图片 URL 跟着换（`panelSrc` 带 `m=`），
      // 浏览器不会继续吃旧缓存；派生元数据（位图像素尺寸、cost）原地同步。
      //
      // **删掉的素材不动文档对象**：面板留在画布上，经既有的缺失素材语义
      // （preflight 的 `missing-asset` + 重新链接）交给用户处置。自动删对象
      // 就是拿一次网盘掉线换用户的排版。
      void refreshAssetsAndSync({ affectedIds: affectedAssetIdsOf(ev) })
      break
    }

    case 'project.error': {
      // 后台刷新失败，**可恢复**：内存里的注册表原封不动，watcher 继续跑，
      // 文件修好之后下一轮自动重试。所以它是一条常驻的状态提示，不是模态框
      // ——没有需要用户当场做的决定。
      const known = i18n.exists(`backend.${ev.code}`, { ns: 'errors' })
      setStatus(
        known
          ? msg(`backend.${ev.code}`, ev.params ?? {}, 'errors')
          : msg('status.projectBackgroundError', undefined, 'workspace'),
        'error',
      )
      break
    }

    case 'probe.started':
      // 「运行并发现图」的执行确认：starting_runtime → running
      useScriptRunStore.getState().markRunning(ev.script)
      break

    case 'native.session':
      // `tavotto run` 的会话状态（ADR 0021 §5.1）。后端发的是**快照**不是
      // 增量，落地按 `sequence` 判序——断线重连补发的旧事件不该把已经退出
      // 的脚本显示成"正在运行"。
      useNativeSessionStore.getState().applyEvent(ev.session)
      break

    case 'registry.changed': {
      // 注册表变了（本标签页 probe 成功 / 另一标签页登记 / 手工裁决 /
      // 外部编辑器新增或删除脚本）：脚本清单与 runtime 素材清单都要重取
      // ——但只重取**已经取过的**，没打开过素材面板的标签页不必为别人的
      // 登记发请求
      const lib = useScriptLibraryStore.getState()
      if (lib.loaded) void lib.load()
      const runtime = useRuntimeAssetStore.getState()
      if (runtime.assets !== null) void runtime.loadAssets()
      // 素材清单 + 画布上已有面板的派生元数据（`script` 就在这一步原地变的）。
      // 这里**不看 `conflicts`**：缺席 = 这一轮没跑静态扫描，不是"没有冲突"，
      // 拿它去改界面等于把"没测量"当成"测量结果是零"。
      void refreshAssetsAndSync()
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
      subscribeEvents(handleServerEvent, () => {
        // 后端重启后 SSE 会重连，借这个时机让版本自检立刻复查一次
        window.dispatchEvent(new Event('mm:sse-open'))
        // 断线期间发生的事件全都没收到：补一次素材刷新 + 派生同步（节流）
        recoverAfterReconnect()
      }),
    [],
  )
}
