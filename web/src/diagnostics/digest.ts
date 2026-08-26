/**
 * 文档状态摘要（ADR 0016 §5 末段 / §15）。
 *
 * 诊断要回答的是「撤销之后，文档回到之前那个状态了吗」——那需要一个**同一性
 * 判据**，而不是一个自增计数器（计数器只会一路变大，永远说不出「回到了
 * 上一个状态」）。所以要 hash，但**不能每次 commit 都把整个文档 stringify
 * 一遍**：granular 历史模式下打一个字就是一次 commit。
 *
 * 解法是把结构共享用起来。documentStore 走 immer，一次 commit 只会换掉**被改
 * 到的那几个对象**的引用，其余对象的引用逐字不变。于是按对象引用做 WeakMap
 * 缓存：打一个字 → 只有那一个对象重新 hash，其余 N-1 个直接命中缓存，
 * 文档级摘要再把 N 个短 hash 折一次（N 个 12 字符的串，几微秒）。
 *
 * WeakMap 意味着对象被回收时缓存条目自动消失，缓存本身不会变成泄漏源。
 */
import { docHash, diagnosticHash } from './hash'
import type { CanvasObject, FigureDocument } from '@/types/document'

/** 对象引用 → 它的内容 hash。immer 的结构共享让命中率在编辑期接近 100% */
const objectDigests = new WeakMap<object, string>()
/** 文档引用 → 文档摘要。同一次 commit 里 before/after 各被问一次 */
const docDigests = new WeakMap<object, string>()

/**
 * 单个对象的内容 hash。**整个对象都参与**——图内 override 的值、标注文字、
 * 位置、锁定状态全都算数，因为它们全都是「文档状态」的一部分。
 *
 * 这里是全仓库唯一一处把用户内容喂进 hash 的地方，而 `diagnosticHash` 只
 * 返回 12 位十六进制：进去的是内容，出来的是身份。
 */
function digestObject(o: CanvasObject): string {
  const hit = objectDigests.get(o)
  if (hit) return hit
  const d = diagnosticHash(o)
  objectDigests.set(o, d)
  return d
}

/**
 * 文档摘要。相同内容 → 相同摘要；任何一处改动 → 不同摘要。
 *
 * 参与摘要的是「文档 schema 里的东西」：页面设置、对象、参考线、布局组、
 * 出版规范绑定。文档名也算——改名同样是一次文档改动。
 */
export function documentDigest(doc: FigureDocument | null | undefined): string {
  if (!doc) return docHash(null)
  const hit = docDigests.get(doc)
  if (hit) return hit
  const parts: unknown[] = [
    doc.name,
    doc.page,
    doc.guides,
    doc.layoutGroups ?? null,
    doc.profile ?? null,
    doc.objects.map(digestObject),
  ]
  const d = docHash(parts)
  docDigests.set(doc, d)
  return d
}
