/**
 * 样式 / 规范内容上的点分路径读写（`element.title.fontsize`、`widths_mm.single`）。
 *
 * 设置里样式页与规范页的编辑区都经这里改草稿：**返回新对象、不改入参**，
 * 「这份配置没管这一项」是独立一档（`clearPath` 把它整段删掉，不是写一个默认值）。
 */

export function readPath(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (acc, key) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[key] : undefined),
    obj,
  )
}

/**
 * 写一个点分路径，**返回新对象**（不改入参）。路径上缺的层补成空对象；
 * 撞上非对象（用户导入的怪东西）就整段替换，不静默丢掉这次修改。
 */
export function writePath(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const [head, ...rest] = path.split('.')
  const next = { ...obj }
  if (!rest.length) {
    next[head] = value
    return next
  }
  const child = next[head]
  next[head] = writePath(
    child && typeof child === 'object' && !Array.isArray(child)
      ? (child as Record<string, unknown>)
      : {},
    rest.join('.'),
    value,
  )
  return next
}

/** 把一个点分路径整段删掉（回到「这份配置没管这一项」那一档）；删空的上层一并删掉。 */
export function clearPath(obj: Record<string, unknown>, path: string): Record<string, unknown> {
  const [head, ...rest] = path.split('.')
  const next = { ...obj }
  if (!rest.length) {
    delete next[head]
    return next
  }
  const child = next[head]
  if (!child || typeof child !== 'object' || Array.isArray(child)) return next
  const pruned = clearPath(child as Record<string, unknown>, rest.join('.'))
  if (Object.keys(pruned).length) next[head] = pruned
  else delete next[head]
  return next
}

/**
 * 配置里写着控件认不出的值（导入的、前向版本的：字号 `"large"`、字体是一串候选、线宽是字符串）时
 * 照原值说出来。设置里**有值就不许显示成「未设置」**——不点就原样保存、应用时也可能被读到，
 * 说它不在是一句假话；它要能看见、也要能单独清掉。
 */
export function rawValueText(v: unknown): string {
  if (typeof v === 'string') return JSON.stringify(v)
  if (Array.isArray(v)) return v.map(String).join(', ')
  return JSON.stringify(v) ?? String(v)
}
