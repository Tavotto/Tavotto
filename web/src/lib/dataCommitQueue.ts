/**
 * 数据出图的「提交」串行锁：单份出图（临时标签里点一张）与批量出图（一次选多份、各画一张）共用。
 * 后端每次提交都重算可用的文件名，两个提交并发会抢同一个名字，所以任何时刻只许一个提交在飞。
 * 一条 promise 队列：先到先做，前一个不管成败，后一个才开始；调用方拿到自己那一个的结果。
 */
let tail: Promise<unknown> = Promise.resolve()

export function runExclusive<T>(fn: () => Promise<T>): Promise<T> {
  const next = tail.then(fn, fn)
  tail = next.catch(() => undefined)
  return next
}

/** 测试隔离：丢掉排队的（不等在飞的），下一个提交立刻开始 */
export function resetCommitQueue(): void {
  tail = Promise.resolve()
}
