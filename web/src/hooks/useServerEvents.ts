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
import { usePackageStore } from '@/store/packageStore'
import { useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { applyExportJob } from '@/store/exportStore'
import { recoverAfterReconnect, refreshAssetsAndSync } from '@/store/liveSync'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import {
  renderFailureOwner,
  settleRenderFailureToast,
  useRenderStore,
} from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'

const short = (id: string) => id.split('/').pop()?.replace(/\.[^.]+$/, '') ?? id
const stemOf = (fileId: string) => short(fileId)
/** 后端稳定错误码 → 当前语言的一句话（参数为空的那些） */
const translateBackend = (code: string) => t(`backend.${code}`, { ns: 'errors' })

/** 冷启动耗时提示；light 没有提示（本来就快） */
const costHint = (cost: string): string =>
  cost === 'heavy' || cost === 'medium'
    ? t(`status.coldHint.${cost}`, { ns: 'workspace' })
    : ''

export { renderFailureOwner }

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
  //
  // 「本标签页的项目」取**此刻认领的** pj：切项目的换代期间 `project` 还是旧值，拿它判的话，
  // 旧项目在这段窗口里发来的事件会被放进来、写进已经换过代的 store（#589）。
  const mine = currentProjectId() ?? useProjectStore.getState().project?.id
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

    case 'engine.environment_adopted':
      // 跑前的门自动改用了用户自己的环境（ADR 0079）：通知轨上说一句并给「改回」。带 pj，
      // 上面那道判别已经挡掉别的项目的
      useEnvStore.getState().noteEnvironmentAdopted({
        source: ev.source,
        label: ev.label,
        python_version: ev.python_version,
      })
      break

    case 'engine.package':
      // 包管理作业的进度（ADR 0038）。同样按 job_id 走，作业绑定项目。
      usePackageStore.getState().onProgress(ev)
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
          'info',
          { passive: true },
        )
      }
      break
    }
    case 'render.done':
      render.noteBuilding(ev.id, null)
      // 同一张图先前那条「无法渲染」可能已经不成立了（修好依赖后自动重渲染成功）：这个文件没有
      // 一个变体还坏着 / 还在渲染时撤掉它。只认同一个主人——挂着的是别的图 / 别的类别的提示就不动
      settleRenderFailureToast(ev.pj, ev.id)
      // 被动通知：不顶掉用户刚触发的那句结果（「已修复 N 项」之类，见 uiStore.statusPassive）
      setStatus(msg('status.renderDone', { name: short(ev.id) }, 'workspace'), 'info', {
        passive: true,
      })
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
        { owner: renderFailureOwner(ev.pj, ev.id) },
      )
      break

    case 'script.input_requested':
      // 脚本里的 input() 在等作答（ADR 0099）：带 pj，上面那道判别已经挡掉别的项目的
      useScriptInputStore.getState().onRequested({
        id: ev.id,
        script: ev.script,
        index: ev.index,
        input_kind: ev.input_kind,
        prompt: ev.prompt ?? '',
        stdout_tail: ev.stdout_tail ?? '',
        secret: ev.secret === true,
        suggestion: ev.suggestion ?? null,
        recheck: ev.recheck ?? null,
      })
      break
    case 'stream.hello':
      // 能答题的事件流连上了：记下流 id，报一次在看哪个项目（后端只把在看那个项目的流算作答题方）
      useScriptInputStore.getState().onStreamHello(ev.stream_id)
      break
    case 'input_remap_changed':
      // 改指表换代（ADR 0106 §五）：按代次去重——发起的窗口收到接口响应时已经作废过这一代
      useEnvStore.getState().onInputRemapChanged(ev.generation, ev.reason)
      break
    case 'script.input_closed':
      useScriptInputStore.getState().onClosed(ev.id)
      break
    case 'script.input_autofilled':
      useScriptInputStore.getState().onAutofilled(ev.script, ev.answer)
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
      // 设置里的改写备份：状态（「恢复原脚本」还是「只撤销那几处」）是按磁盘现算的，脚本一变就重读——
      // 挂着旧状态点下去是整份覆盖（Codex 评 #730 P1；后端另有锁内哈希核对）
      useEnvStore.getState().bumpScriptBackups()
      // 重建**不等**素材刷新：脚本变了而它产出的 PDF 还没重新生成时，
      // /api/panels 里的 mtime 一动不动，等它等不来。派生元数据的同步照常
      // 跟在刷新后面（走合并入口，与同一批里的其它事件共用一个请求）。
      void refreshAssetsAndSync()
      // AI 那条路紧跟着一条 `ai.done` 在说同一件事：一次修改只留一条提示；改记住的输入（script_input）
      // 是用户在答案管理里刚点的，那边自己说「正在重新运行」，这里不再说「脚本已更新」
      if (affected.length && ev.reason !== 'ai' && ev.reason !== 'script_input') {
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
      // runtime 素材清单跟着重取（同 `registry.changed`：只重取已经取过的）。
      // 「哪张图有自己的原件」正是在素材变化那一刻改变的：脚本在外面 savefig
      // 出了 PDF，后端清单里那条 runtime 素材就该让位给 FileAsset——不重取的话
      // 「尚未运行」的运行时卡与同名 PDF 卡会并排挂到下一次注册表变化为止
      // （UI 审计 T06 看到的正是这一幕）。
      const runtimeAssets = useRuntimeAssetStore.getState()
      if (runtimeAssets.assets !== null) void runtimeAssets.loadAssets()
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

    case 'project.scan':
      // 导入即扫描有进展：只是「重读」提示，快照以 GET 为准（SSE 丢了轮询也会补上）
      void useProjectScanStore.getState().refresh()
      break

    case 'ai.delta':
      // 按 sid 写进自己的会话；不是本标签页此刻持有的会话就什么都不做
      useAiStore.getState().appendDelta(ev.session, ev.kindOf ?? 'message', ev.text)
      break

    case 'ai.done': {
      const ai = useAiStore.getState()
      // **只对自己持有的会话起副作用**（提示、失败说明……）：老后端的 `ai.done` 不带 pj，上面那道
      // 项目判据拦不住它；切项目之后 A 的会话已不在列表里，这里就一个字都不说（#589）
      if (!ai.finish(ev)) break
      // 这里**不再 markStale**：文件变了的话后端在 ai.done 之前已经作废 worker、
      // 跑过统一刷新、发过 `panel.file_changed`（reason=ai），上面那个分支按
      // stem 把画布上的面板全部转入跟踪——同一次修改只置一次 stale（ADR 0041）。
      // 老后端（没有 refresh 字段）仍靠 watcher 的 panel.file_changed 兜底。
      const refreshFailed = ev.changed && ev.refresh?.status === 'failed'
      if (refreshFailed) {
        // 代码改成了、项目没刷新：两件事都说，不把前者伪装成全部成功
        const code = ev.refresh?.code ?? ''
        const reason = i18n.exists(`backend.${code}`, { ns: 'errors' })
          ? translateBackend(code)
          : code
        setStatus(msg('status.aiChangedRefreshFailed', { reason }, 'ai'), 'error')
        break
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
  // 换了项目就重报一次「这条能答题的事件流在看哪个项目」（ADR 0099 §五）。跟**认领**走、不跟 `project`
  // 字段走：事件过滤从认领那一刻就换成新项目了，`project` 要等换代做完才赋值（Codex #680 P1）
  useEffect(
    () => onCurrentProjectChange((pj) => useScriptInputStore.getState().announce(pj)),
    [],
  )
  useEffect(
    () =>
      subscribeEvents(handleServerEvent, () => {
        // 后端重启后 SSE 会重连，借这个时机让版本自检立刻复查一次
        window.dispatchEvent(new Event('mm:sse-open'))
        // 断线期间发生的事件全都没收到：补一次素材刷新 + 派生同步（节流）
        recoverAfterReconnect()
        // 还在等作答的脚本输入（ADR 0099）也要接回来；顺带取记住的答案（脚本行的入口靠它）
        void useScriptInputStore.getState().loadAnswers()
        // 断线期间可能丢了 `input_remap_changed`：拉一次此刻的代次，落后就补一次作废（ADR 0106 §五）
        catchUpInputRemap()
      }),
    [],
  )
  // 页面恢复（切回标签页 / 唤醒）同理：休眠期间事件流可能断过而没触发重连
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') catchUpInputRemap()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])
}

/** 补拉改指表代次：环境刷新里带着它，`envStore.refresh` 交给 `noteInputRemapGeneration` 去重 / 补作废 */
export function catchUpInputRemap(): void {
  if (useProjectStore.getState().phase !== 'open') return
  void useEnvStore.getState().refresh()
}
