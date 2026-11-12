import { create } from 'zustand'
import { discardAiAttachment, uploadAiAttachment } from '@/lib/api'
import { ATTACH_MAX, ATTACH_MAX_TOTAL_BYTES, pickFiles, prepareAttachment, type AttachKind } from '@/lib/aiAttachments'

/**
 * 助手输入框里附的文件（ADR 0175）：图片给模型看，其余原样附上。按「项目 × 面板文件」分槽，与草稿同一把键：切页签 / 切面板再回来还在。
 * 只活在页面内存里：文件一加进来就传给后端暂存（`/api/ai/attachments`），这里只记后端给的 id 和一张几 KB 的缩略图；
 * 刷新页面就没了（后端的暂存一小时后自己清）。发送时由 `settle` 等还在上传的文件收尾，再把 id 交给 `aiStore.start`。
 */
export interface AttachItem {
  /** 本地键（上传前就有，列表渲染与去除都认它） */
  key: string
  kind: AttachKind
  name: string
  /** 扩展名角标（小写、无点）；没有就空 */
  ext: string
  size: number
  /** 图片的缩略图；文件为空串 */
  thumb: string
  status: 'uploading' | 'ready'
  /** 后端暂存 id；`status === 'ready'` 才有 */
  serverId?: string
}

/** 加文件的结局：只给个数，说什么话由调用方（界面）定。没有任何一种结局会让流程停下 */
export interface AddSummary {
  /** 超过个数 / 合计上限被丢掉的 */
  overflow: number
  /** 单个超过上限的 */
  tooLarge: number
  /** 传给后端没成功的 */
  uploadFailed: number
}

interface AttachState {
  byTarget: Record<string, AttachItem[]>
  add: (target: string, pj: string | null, files: readonly File[]) => Promise<AddSummary>
  remove: (target: string, pj: string | null, key: string) => void
  /** 等这个槽里还在上传的文件落定，回已经就绪的（发送前调） */
  settle: (target: string) => Promise<AttachItem[]>
  /** 发送交给了提交之后：从槽里拿掉这次提交带走的那几个（按本地键），**不**删后端暂存（文件已经被任务拿走 / 提交自己持有 id）；
   *  提交途中新加的文件留在输入框里 */
  release: (target: string, keys: readonly string[]) => void
}

let seq = 0
/** 上传中的承诺：`settle` 等它们 */
const inflight = new Map<string, Promise<void>>()
/** 每个槽里还没结束的 `add`（含读文件 / 解码阶段，那时还没上卡）：`settle` 先等它们 */
const pendingAdds = new Map<string, Set<Promise<unknown>>>()

export const attachTargetKey = (project: string | null, fileId: string | null): string => `${project ?? ''}\u0000${fileId ?? ''}`

const totalOf = (items: readonly AttachItem[]) => items.reduce((n, x) => n + x.size, 0)

export const useAiAttachStore = create<AttachState>((set, get) => ({
  byTarget: {},

  add: (target, pj, files) => {
    const run = addInto(target, pj, files)
    const pending = pendingAdds.get(target) ?? new Set<Promise<unknown>>()
    pendingAdds.set(target, pending)
    pending.add(run)
    const done = () => {
      pending.delete(run)
      if (!pending.size) pendingAdds.delete(target)
    }
    run.then(done, done)
    return run
  },

  remove: (target, pj, key) => {
    const item = get().byTarget[target]?.find((x) => x.key === key)
    set((s) => ({ byTarget: { ...s.byTarget, [target]: (s.byTarget[target] ?? []).filter((x) => x.key !== key) } }))
    if (item?.serverId) void discardAiAttachment(item.serverId, pj).catch(() => undefined)
  },

  settle: async (target) => {
    // 先等还在读 / 解码 / 上传的整批加入（发送那一刻的快照；之后再加的不等）
    await Promise.allSettled([...(pendingAdds.get(target) ?? [])])
    const keys = (get().byTarget[target] ?? []).map((x) => x.key)
    await Promise.all(keys.map((k) => inflight.get(k)).filter((p): p is Promise<void> => !!p))
    return (get().byTarget[target] ?? []).filter((x) => x.status === 'ready' && x.serverId)
  },

  release: (target, keys) =>
    set((s) => {
      const drop = new Set(keys)
      const left = (s.byTarget[target] ?? []).filter((x) => !drop.has(x.key))
      if (left.length) return { byTarget: { ...s.byTarget, [target]: left } }
      const { [target]: _gone, ...rest } = s.byTarget
      return { byTarget: rest }
    }),
}))

async function addInto(target: string, pj: string | null, files: readonly File[]): Promise<AddSummary> {
  const { getState: get, setState: set } = useAiAttachStore
  const room = ATTACH_MAX - (get().byTarget[target]?.length ?? 0)
  const { take, overflow } = pickFiles(files, room)
  const summary: AddSummary = { overflow, tooLarge: 0, uploadFailed: 0 }
  // 按选中的顺序逐个处理（并行的话先解完的先上卡，顺序会乱）；上传本身仍然并行，下面一起等
  const jobs: Promise<void>[] = []
  for (const file of take) {
    await (async () => {
      const prepared = await prepareAttachment(file)
      if (!prepared.ok) {
        summary.tooLarge += 1
        return
      }
      const { item: p } = prepared
      // 解码是异步的：等的途中别的文件可能已经占满了名额 / 合计上限
      const now = get().byTarget[target] ?? []
      if (now.length >= ATTACH_MAX || totalOf(now) + p.size > ATTACH_MAX_TOTAL_BYTES) {
        summary.overflow += 1
        return
      }
      const key = `att${++seq}`
      const item: AttachItem = { key, kind: p.kind, name: p.name, ext: p.ext, size: p.size, thumb: p.thumb, status: 'uploading' }
      set((s) => ({ byTarget: { ...s.byTarget, [target]: [...(s.byTarget[target] ?? []), item] } }))
      const job = uploadAiAttachment(p.blob, p.name, pj).then(
        (res) => {
          // 上传期间用户已经点 × 去掉了：后端那份也删掉
          const still = get().byTarget[target]?.some((x) => x.key === key)
          if (!still) {
            void discardAiAttachment(res.id, pj).catch(() => undefined)
            return
          }
          set((s) => ({
            byTarget: {
              ...s.byTarget,
              [target]: (s.byTarget[target] ?? []).map((x) => (x.key === key ? { ...x, status: 'ready', serverId: res.id } : x)),
            },
          }))
        },
        () => {
          summary.uploadFailed += 1
          set((s) => ({ byTarget: { ...s.byTarget, [target]: (s.byTarget[target] ?? []).filter((x) => x.key !== key) } }))
        },
      )
      inflight.set(key, job)
      jobs.push(job.then(() => void inflight.delete(key)))
    })()
  }
  await Promise.all(jobs)
  return summary
}
