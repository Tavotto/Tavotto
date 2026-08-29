/**
 * 接入状态 → 人话。**唯一的一份**。
 *
 * 素材卡的角标、素材面板的说明条、接入中心的每一行、属性栏的那句提示，
 * 说的都是同一件事。各写一遍的话，同一张图在四个地方会有四种措辞——那正是
 * Session 07 之前的病（三个界面三个答案），只不过从"判据不一致"退化成
 * "措辞不一致"。
 *
 * 两条纪律：
 *
 * 1. **按 `reason_code` 查句子，不按 `status`。** 同一个状态下不同 code 要说
 *    的话完全不同：`auto_linkable` 的四个 code 里，一个是「马上就好」，
 *    另外三个是「不做点什么就永远不会好」。只看状态的话，只读项目里的用户
 *    会一直等一个永远不来的结果。
 * 2. **不向普通用户暴露实现术语**（registry / stem / entry / AST / probe）。
 *    这正是 reason code 存在的理由：后端给枚举，前端给人话。精确名词只出现在
 *    每一行的「技术详情」里——那一段明确是给排障用的。
 */
import { t as translate } from '@/i18n'
import type { PanelCapability, ReadinessStatus, ReadinessSummary } from '@/lib/api'

/** 六个状态的短标签：可编辑 / 待连接 / 需试运行 / 有冲突 / 源脚本丢失 / 仅排版 */
export const statusLabel = (status: ReadinessStatus): string =>
  translate(`readiness.status.${status}`, { ns: 'workspace' })

/**
 * 一句话原因。句子里要提到的脚本按下面这条取：
 *
 * * 已经绑定的（`editable` / `source_missing`）用 `script`；
 * * 还没绑定的（`auto_linkable` 的四个 code）用**唯一那个**候选。
 *
 * **候选绝不写进 `script`**（后端的纪律，见 `api.ts`），所以这里也不能反过来
 * 把它们合成一个字段——合并之后「已经连上了」与「找到了但还没连」在界面上
 * 就分不出来了。
 */
export const reasonText = (cap: PanelCapability): string =>
  translate(`readiness.reason.${cap.reason_code}`, {
    ns: 'workspace',
    script: cap.script ?? cap.candidates[0] ?? '',
  })

/**
 * 「待连接」是哪几个状态——**这一份是唯一的定义**。
 *
 * `editable`（已经好了）与 `layout_only`（没有源脚本，但那不是问题）不在其中。
 * 这是个**集合不是顺序**：接入中心里那一组仍按报告自己的 id 序排（后端保证
 * 稳定），列表不该因为状态变了就跳位置。
 */
export const PENDING_STATUSES: readonly ReadinessStatus[] = [
  'conflict',
  'needs_probe',
  'auto_linkable',
  'source_missing',
]

/**
 * 「待连接」的张数。横幅说一个总数、接入中心顶部也说一个——**必须是同一个
 * 加法**，各自展开写一遍的话，将来多一个状态时总有一处会漏掉它，而用户看到
 * 的是两个界面对同一个项目报出不同的数。
 *
 * 从 `PENDING_STATUSES` 现算，不再手写一串加号：集合与和只有一个出处。
 */
export const pendingCount = (summary: ReadinessSummary): number =>
  PENDING_STATUSES.reduce((n, s) => n + summary[s], 0)
