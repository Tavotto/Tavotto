import { t } from '@/i18n'
import type {
  ArrowHeadType,
  ArrowObject,
  CanvasObject,
  EndPoint,
  ObjectBase,
  ShapeKind,
  ShapeObject,
} from '@/types/document'
import { arrowHeads, legacyHead, lineEndpoints } from '@/types/document'

/**
 * 画布标注的**类型切换**：矩形 ↔ 椭圆 ↔ 其它形状、直线 ↔ 箭头。
 *
 * 这里是这件事的唯一出处——「能切成什么」「切完长什么样」两个问题都只在本模块
 * 回答，属性栏的对象标题与右键菜单的「更改为 ›」都只是它的两个入口，各自不许
 * 再判一遍。切换是**纯函数**：给一个对象与一个目标，返回换过类型的新对象，
 * 不碰 store、不开事务、不进历史——那三件事在 `store/actions.switchObjectKind`。
 *
 * ## 为什么是「族」而不是「随便换」
 *
 * `line` 与 `arrow` 的几何是**两个端点**（包围盒比例坐标），其余形状的几何是
 * **包围盒本身**。把一个矩形换成直线，得凭空替用户决定那条线从哪画到哪；把一条
 * 拖出来的箭头换成矩形，它那个被钳到 0.01mm 的包围盒会变成一条几乎不可见的
 * 细缝。两次都不是「同一个东西换个样子」，而是「替用户重画了一个」。所以只在
 * 族内互换：**族内几何一个字不动**，这条是本模块的不变式。
 *
 * 文字与面板整个不参与：文字的内容与排版、面板的素材与 override 都没有对应物。
 *
 * ## 字段的去留只有一条判据：**目标类型会不会读它**
 *
 * 读得到的带过去，读不到的删掉（`sides` 只有 polygon 读、`cornerRadius` 只有
 * rect 读、`start`/`end` 只有 line 与 arrow 读、`head*` 只有 arrow 读）。
 * 「留着反正也不画」是脏字段：它会跟着文档存进磁盘、跟着载荷发给后端、跟着
 * 样式剪贴板扩散，而下一个读它的人不知道它是上一个类型剩下的。
 *
 * 丢掉的值由**撤销**负责——切换是一条 commit、一条历史，撤销回来的是逐字段
 * 原样的那个对象（补丁携带旧值），不是「按默认值重建的近似品」。
 */

/** 可切换的类型标识：形状用 `ShapeKind`，箭头是自己一档（`ShapeKind` 里没有它） */
export type SwitchKind = ShapeKind | 'arrow'

export type SwitchFamily = 'box' | 'linear'

/**
 * 类型 → 族。**写成 `Record<SwitchKind, …>` 而不是两个数组的 includes**：
 * 将来给 `ShapeKind` 加一种形状时，这张表少一格就编译不过——必须当场回答
 * 「它属于哪个族」。靠数组的话新形状会安静地落进默认分支，界面上表现为
 * 「切过去之后几何被重画了」，而没有任何一处会红。
 */
const FAMILY_OF: Record<SwitchKind, SwitchFamily> = {
  rect: 'box',
  ellipse: 'box',
  triangle: 'box',
  diamond: 'box',
  polygon: 'box',
  brace: 'box',
  line: 'linear',
  arrow: 'linear',
}

/**
 * 每族的目标集合，**顺序即界面里的排列顺序**（常用在前）。
 * 与 `FAMILY_OF` 的一致性由 `shapeSwitch.test.ts` 双向看住：
 * 表里的每一种都要出现在自己族的清单里，清单里的每一种也都得在表里。
 */
export const SWITCH_FAMILY_KINDS: Record<SwitchFamily, readonly SwitchKind[]> = {
  box: ['rect', 'ellipse', 'triangle', 'diamond', 'polygon', 'brace'],
  linear: ['line', 'arrow'],
}

/** 多边形默认边数。与 `ShapeSection` / `exportPayload` / 后端 `_draw_shape` 的读取缺省同一个数 */
export const DEFAULT_SIDES = 6

/** 直线换成箭头时的默认端型：起点无、终点实心三角（与画布工具新建的箭头同一个默认） */
const DEFAULT_HEADS: { start: ArrowHeadType; end: ArrowHeadType } = {
  start: 'none',
  end: 'triangle',
}

/** 对象当前的可切换类型；文字 / 面板返回 null（不参与） */
export function switchKindOf(o: CanvasObject): SwitchKind | null {
  if (o.type === 'arrow') return 'arrow'
  if (o.type === 'shape') return o.shape
  return null
}

export function switchFamilyOf(kind: SwitchKind): SwitchFamily {
  return FAMILY_OF[kind]
}

/**
 * 这批对象能切成哪些类型。**空数组 = 不提供切换**，三种成因合成一个答案：
 * 空选区 / 有对象不参与（文字、面板）/ 跨了族。
 *
 * 多选按「全部同族」放行，切换作用于全部——族内几何不动，所以对每个成员分别
 * 成立的事，对一批也成立。族不同就整个不给：一次点击同时把矩形变成箭头、把
 * 箭头变成矩形，没有一个说得清的语义。
 */
export function switchTargets(objs: readonly CanvasObject[]): readonly SwitchKind[] {
  if (!objs.length) return []
  const families = new Set<SwitchFamily>()
  for (const o of objs) {
    const kind = switchKindOf(o)
    if (kind == null) return []
    families.add(FAMILY_OF[kind])
  }
  if (families.size !== 1) return []
  const [family] = families
  return SWITCH_FAMILY_KINDS[family]
}

/**
 * 这批对象**共同的**当前类型；取值不一致返回 null。
 *
 * 与 `switchTargets` 是两个问题：一批矩形 + 椭圆同族（给得出目标集合），但
 * 「当前是哪一种」没有答案——界面上该显示成「多个值」，而不是挑第一个冒充。
 */
export function sharedSwitchKind(objs: readonly CanvasObject[]): SwitchKind | null {
  if (!objs.length) return null
  const first = switchKindOf(objs[0])
  if (first == null) return null
  return objs.every((o) => switchKindOf(o) === first) ? first : null
}

/**
 * 类型名。**是函数不是常量表**——常量在模块求值那一刻就把当时的语言定死了
 * （与 `types/document.objectTypeLabel` 同一条理由）。
 *
 * 文案键与 `objectLabel` 的兜底分支读的是同两组（`common:shape.*` /
 * `common:objectType.arrow`），译文只有那一份。
 */
export function switchKindLabel(kind: SwitchKind): string {
  return kind === 'arrow'
    ? t('objectType.arrow', { ns: 'common' })
    : t(`shape.${kind}`, { ns: 'common' })
}

/**
 * 换类型。**返回 null 表示这一次什么都不该发生**：目标就是当前类型、对象不参与
 * 切换、或者跨了族。调用方拿到 null 就不要开事务——「切到自己」多一条撤销记录，
 * 用户按撤销时会觉得撤销坏了。
 */
export function switchObject(o: CanvasObject, target: SwitchKind): CanvasObject | null {
  // 先把「不参与切换」挡在外面：这一句同时给了 TS 一个收窄，下面才能直接读
  // 箭头与形状的共有样式
  if (o.type !== 'arrow' && o.type !== 'shape') return null
  const from = switchKindOf(o)
  if (from == null || from === target) return null
  if (FAMILY_OF[from] !== FAMILY_OF[target]) return null

  const carried = carryOver(o)
  const ends = endpointsOf(o)

  if (target === 'arrow') {
    const heads = o.type === 'arrow' ? arrowHeads(o) : DEFAULT_HEADS
    return {
      ...carried,
      type: 'arrow',
      start: ends.start,
      end: ends.end,
      headStart: heads.start,
      headEnd: heads.end,
      // 旧 `head` 字段同步维护：老构建 / 老后端读到这支箭头时仍有合理的端型
      head: legacyHead(heads),
    } satisfies ArrowObject
  }

  return {
    ...carried,
    type: 'shape',
    shape: target,
    // `fill` 是 ShapeObject 的**必填**字段：从箭头切过来时没有源值，给「无填充」
    // （与画布工具新建的形状同一个默认）。形状之间切换照常带着自己的填充。
    fill: o.type === 'shape' ? o.fill : null,
    ...(o.type === 'shape' && o.fillOpacity != null ? { fillOpacity: o.fillOpacity } : {}),
    ...(target === 'line' ? { start: ends.start, end: ends.end } : {}),
    ...(target === 'polygon' ? { sides: DEFAULT_SIDES } : {}),
  } satisfies ShapeObject
}

/**
 * 「随类型走」的字段全集。切换时**一律先清空**，再由目标补回它读得到的那些。
 *
 * 少列一个 = 脏字段留在了新类型上（矩形的圆角跟去椭圆、多边形的边数跟去矩形）；
 * 多列一个 = 共有样式被切没了（描边色 / 线宽 / 线型是两种类型都读的）。**两个
 * 方向都由下面那两条编译期断言看住**，不靠人记得回来改。
 *
 * 判据是**目标类型会不会读它**：`sides` 只有 polygon 读、`cornerRadius` 只有
 * rect 读、`start` / `end` 只有 line 与 arrow 读、`head*` 只有 arrow 读。
 * `fill` / `fillOpacity` 也在表里——它们是 `type: 'shape'` 独有的字段，切成箭头
 * 时结构上就没有地方放；切回形状时由上面补回（`fill` 必填，缺省「无填充」）。
 */
const KIND_FIELDS = [
  'shape',
  'fill',
  'fillOpacity',
  'cornerRadius',
  'sides',
  'start',
  'end',
  'head',
  'headStart',
  'headEnd',
] as const

type KindKey = (typeof KIND_FIELDS)[number]

/**
 * 箭头与形状**共有**的外观样式：三种类型都读，切换时一路带过去。
 *
 * 单独列出来而不是「不在 `KIND_FIELDS` 里的都算共有」，是为了让下面那条编译期
 * 断言成立——只有把每个字段都明确归到某一类，「有没有漏掉一个」才问得出口。
 * `shapeSwitch.test.ts` 也按这张表逐项断言「切换后原样保留」，所以将来多一个
 * 共有样式，它自动多一条用例。
 */
export const SHARED_STYLE_FIELDS = ['strokePt', 'color', 'dash'] as const
type SharedStyleKey = (typeof SHARED_STYLE_FIELDS)[number]

/** 联合类型的**全部**键（`keyof (A | B)` 只给交集，这里要的是并集） */
type AllKeys<T> = T extends unknown ? keyof T : never
type MarkKey = AllKeys<ArrowObject | ShapeObject>

/**
 * 分类的完整性，两个方向各一条，**编译期**看住。
 *
 * 标注对象上的每个键必须恰好属于四类之一：`ObjectBase`（跟对象走）、`type`、
 * `KIND_FIELDS`（跟类型走，切换时清掉再按目标补）、`SHARED_STYLE_FIELDS`
 * （共有外观，原样带过去）。`Exactly<T>` 只接受 `never`：给 `ArrowObject` /
 * `ShapeObject` 加一个新字段而没归类，`UnclassifiedMarkField` 就不是 never，
 * 这个文件当场编译不过——而不是等到某天用户发现「换成椭圆之后圆角还在」。
 * 反方向同理：`KIND_FIELDS` 里登记了一个标注对象上根本没有的名字（改名、
 * 手滑）也当场红。
 *
 * 这条纪律做进结构，是因为靠「记得回来改」维持的纪律等于没有：新字段会安静地
 * 被 `carryOver` 当共有样式带到新类型上，而两侧渲染器都不读它，界面上一点异常
 * 都看不出来。
 */
type Exactly<T extends never> = T
export type UnclassifiedMarkField = Exactly<
  Exclude<MarkKey, keyof ObjectBase | 'type' | KindKey | SharedStyleKey>
>
export type UnknownKindField = Exactly<Exclude<KindKey, MarkKey>>

/**
 * 切换时**原样带过去**的那一半：`ObjectBase`（id / 几何 / 旋转 / 成组 / 锁定 /
 * 隐藏 / 名字 / 布局固定）+ 共有外观样式。两半都是从 interface 上 `Pick` 出来
 * 的，不是另抄一份字段声明。
 */
type CarriedFields = ObjectBase & Pick<ArrowObject, SharedStyleKey>

function carryOver(o: ArrowObject | ShapeObject): CarriedFields {
  const rest = { ...o } as Record<string, unknown>
  for (const key of KIND_FIELDS) delete rest[key]
  delete rest.type
  // 上面两条编译期断言保证了「刨掉 KIND_FIELDS 与 type 之后剩下的正好是
  // CarriedFields」，这个转换才不是一句空话
  return rest as unknown as CarriedFields
}

/**
 * 线状对象的端点。箭头与直线原样取（两者结构同构，`lineEndpoints` 兜住直线的
 * 可选字段）；其余形状没有端点，退回包围盒水平中线——那正是「没有 start/end 的
 * 旧布局文件」的缺省，两侧渲染器都认它。
 *
 * 族内切换其实走不到最后那个分支（box 族没有 line/arrow 目标），留着是因为
 * 判据该说得出所有输入的答案，而不是靠调用点保证不会传进来。
 */
function endpointsOf(o: CanvasObject): { start: EndPoint; end: EndPoint } {
  const raw =
    o.type === 'arrow'
      ? { start: o.start, end: o.end }
      : o.type === 'shape'
        ? lineEndpoints(o)
        : lineEndpoints({})
  // **拷贝而不是引用**：`carryOver` 的 `{ ...o }` 是浅拷贝，端点对象照原样共享。
  // 直接把它挂到新对象上，之后拖动新箭头的端点会同时改掉撤销栈里那个「旧对象」
  // ——撤销回去看着像是没撤销。
  return { start: { ...raw.start }, end: { ...raw.end } }
}
