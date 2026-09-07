/**
 * 「问题在界面上怎么说」的看护（ADR 0030）。
 *
 * 这一层的全部价值就是**不把内部标识说给用户听**，外加把「当前值 / 要求」
 * 从描述符里取对。所以用例只量这两件事，外加双语。
 */
import { describe, expect, it, afterEach } from 'vitest'
import { setLocale } from '@/i18n'
import { SEVERITY_ICON, issueTitle, issueValues, ruleExpectation, severityLabel, subjectName, technicalDetailLines } from './validationText'
import { SEVERITIES, loadProfile } from './profile'
import { runSpec, type PreflightSpec } from './preflight'
import type { ValidationIssue } from './validation'

const issue = (over: Partial<ValidationIssue> = {}): ValidationIssue => ({
  issueId: 'f',
  ruleCode: 'font-too-small',
  severity: 'warn',
  context: 'document',
  objectRef: { documentId: 'd', canvasId: 'c', objectId: 'p1', gid: 'axes_0.xlabel' },
  subject: { kind: 'element', elementLabel: 'X 轴标题', elementRole: 'axis_label' },
  propertyPath: 'fontsize',
  message: { key: 'preflight.fontTooSmall', ns: 'errors', values: { effective: '7.50', min: '8' } },
  technicalDetails: { effective_pt: 7.5, min_pt: 8 },
  fixKind: 'safe_auto',
  ...over,
})

afterEach(() => setLocale('zh-CN'))

describe('主语说人话', () => {
  it('图内元素用引擎给的标签，不是 gid', () => {
    expect(subjectName(issue())).toBe('X 轴标题')
  })

  it('元素名压过面板名：两者都在时说的是元素', () => {
    // 只给 elementLabel 的样例证明不了优先级——把顺序换过来照样绿
    const both = issue({
      subject: { kind: 'element', objectName: '图 1', elementLabel: 'X 轴标题' },
    })
    expect(subjectName(both)).toBe('X 轴标题')
  })

  it('拿不到标签就退到角色名——任何一档都不吐 gid', () => {
    const s = subjectName(issue({ subject: { kind: 'element', elementRole: 'legend' } }))
    expect(s).not.toContain('axes_0')
    expect(s).toBeTruthy()
  })

  it('连角色都没有时说面板名，再没有才说一句通用的', () => {
    expect(subjectName(issue({ subject: { kind: 'element', objectName: '图 1' } }))).toBe('图 1')
    expect(subjectName(issue({ subject: { kind: 'element' } }))).toBe('图内元素')
  })

  it('页面级问题的主语是整张画布', () => {
    expect(subjectName(issue({ subject: { kind: 'page' } }))).toBe('整张画布')
  })

  it('画布对象按类型说话', () => {
    expect(subjectName(issue({ subject: { kind: 'object', objectType: 'arrow' } }))).toBe('箭头')
    expect(subjectName(issue({ subject: { kind: 'object', objectType: 'text' } }))).toBe('标注文字')
  })
})

describe('当前值 → 要求', () => {
  it('字号带单位，要求那一侧说清是「不低于」还是「大于」', () => {
    expect(issueValues(issue())).toEqual({ current: '7.50pt', expected: '≥ 8pt' })
    const floor = issue({
      ruleCode: 'font-below-absolute-floor',
      message: {
        key: 'preflight.fontBelowFloor',
        ns: 'errors',
        values: { effective: '8.00', floor: '8' },
      },
    })
    // 绝对下限**不含等号**：这句话必须说成"大于"，说成"≥"就是在骗人。
    // 8.00 与 8 在屏幕上是同一个数，所以还带上那句解释（见下面的 T41 用例）
    expect(issueValues(floor).expected).toBe('大于 8pt（正好等于不算通过）')
    expect(issueValues(floor).expected).not.toContain('≥')
  })

  it('画布标注那两条用的是 size 参数，同一条规则两种主语', () => {
    const t = issue({
      message: { key: 'preflight.textTooSmall', ns: 'errors', values: { size: '5', min: '8' } },
    })
    expect(issueValues(t).current).toBe('5pt')
  })

  it('没登记的规则不硬编数字，两侧都回 null', () => {
    expect(issueValues(issue({ ruleCode: 'overlap', message: { key: 'preflight.overlap', ns: 'errors' } }))).toEqual({
      current: null,
      expected: null,
    })
  })
})

describe('短标题与技术详情', () => {
  it('短标题按 rule code 查；没登记的退回完整成文，而不是显示 code', () => {
    expect(issueTitle(issue())).toBe('字号偏小')
    const unknown = issueTitle(issue({ ruleCode: 'zzz-unknown' }))
    expect(unknown).not.toContain('zzz-unknown')
    expect(unknown).toContain('7.50')
  })

  it('gid / 对象 id / 属性名只出现在技术详情里', () => {
    const lines = technicalDetailLines(issue())
    expect(lines.join('\n')).toContain('axes_0.xlabel')
    expect(lines.join('\n')).toContain('p1')
    expect(lines.join('\n')).toContain('fontsize')
    // 短标题与主语那两句一个都不许带
    expect(issueTitle(issue())).not.toContain('axes_0')
    expect(subjectName(issue())).not.toContain('axes_0')
  })
})

describe('等级', () => {
  it('四个等级各有图标与文字标签——颜色不是唯一表达', () => {
    for (const s of SEVERITIES) {
      expect(SEVERITY_ICON[s]).toBeTruthy()
      expect(severityLabel(s)).toBeTruthy()
    }
  })

  it('切语言之后跟着换（存的是 key，不是翻好的字符串）', () => {
    expect(issueTitle(issue())).toBe('字号偏小')
    setLocale('en-US')
    expect(issueTitle(issue())).toBe('Font too small')
    expect(subjectName(issue({ subject: { kind: 'page' } }))).toBe('The whole canvas')
  })
})

describe('边界措辞与真实判据同步（审计 T41）', () => {
  /** 一张只有一段画布文字的 spec；文字大小是唯一变量。 */
  const specWithText = (sizePt: number): PreflightSpec => ({
    page: { w_mm: 80, h_mm: 60, margin_mm: 0 },
    panels: [],
    texts: [
      {
        id: 'txt',
        text: 'Ab',
        size_pt: sizePt,
        bold: false,
        font_family: 'Times New Roman',
        rect_mm: [5, 5, 20, 5],
        hidden: false,
      },
    ],
    objects: [],
  })

  /** 一段图内文字：`fontsize × scale` 才是判据看的那个数。 */
  const specWithPanelText = (fontsize: number, scale: number): PreflightSpec => ({
    page: { w_mm: 80, h_mm: 60, margin_mm: 0 },
    panels: [
      {
        id: 'p1',
        name: 'P1',
        kind: 'pdf',
        rect_mm: [0, 0, 80, 60],
        scale,
        manifest: {
          stem: 'Fig1',
          size_mm: [80, 60],
          elements: [
            {
              gid: 'axes_0.xlabel',
              role: 'axis_label',
              label: 'X 轴标题',
              bbox: [0.1, 0.1, 0.5, 0.1],
              draggable: false,
              editable: [
                { prop: 'fontsize', type: 'number', value: fontsize },
                { prop: 'fontfamily', type: 'string', value: 'Times New Roman' },
              ],
            },
          ],
        } as unknown as PreflightSpec['panels'][number]['manifest'],
        px_w: null,
        missing: false,
        stale: false,
        render_error: null,
        unapplied_overrides: 0,
        bitmap_embed: false,
        hidden: false,
      },
    ],
    texts: [],
    objects: [],
  })

  const floorHit = (spec: PreflightSpec) =>
    runSpec(spec, loadProfile()).find((i) => i.id === 'font-below-absolute-floor')

  it('绝对下限的等号边界：求值器判「正好等于」违规，措辞就得这么说', () => {
    const floor = loadProfile().absolute_min_font_size_pt
    // 判据这一侧：正好等于不通过，高一点点就通过（第二句让第一句不会恒真）
    expect(floorHit(specWithText(floor))?.id).toBe('font-below-absolute-floor')
    expect(floorHit(specWithText(floor + 0.01))).toBeUndefined()

    // 措辞这一侧：用求值器**真的吐出来的**那份参数，不是手写的样例
    const hit = floorHit(specWithText(floor))!
    const values = issueValues(
      issue({ ruleCode: 'font-below-absolute-floor', message: hit.message }),
    )
    expect(values.expected).toBe('大于 8pt（正好等于不算通过）')
  })

  it('两个数在屏幕上不一样时不加那句话——它只解释「看起来相等」', () => {
    const hit = floorHit(specWithText(6))!
    expect(
      issueValues(issue({ ruleCode: 'font-below-absolute-floor', message: hit.message })).expected,
    ).toBe('大于 8pt')
  })

  it('7.995pt 被显示成 8.00：同一句话也要成立（这一档的真值并不等于下限）', () => {
    // 图内文字的 `effective` 走 toFixed(2)，7.995×1 显示成 8.00
    const hit = runSpec(specWithPanelText(7.995, 1), loadProfile()).find(
      (i) => i.id === 'font-below-absolute-floor',
    )!
    expect(hit.message.values?.effective).toBe('8.00')
    expect(
      issueValues(issue({ ruleCode: 'font-below-absolute-floor', message: hit.message })).expected,
    ).toBe('大于 8pt（正好等于不算通过）')
  })

  it('含等号的那一侧（≥ / ≤）在边界上说的是「显示已四舍五入」', () => {
    const tooSmall = issue({
      ruleCode: 'font-too-small',
      message: {
        key: 'preflight.fontTooSmall',
        ns: 'errors',
        values: { effective: '8.00', min: '8' },
      },
    })
    expect(issueValues(tooSmall).expected).toBe('≥ 8pt（当前值实际略低，显示已四舍五入）')
    const tooLarge = issue({
      ruleCode: 'font-too-large',
      message: {
        key: 'preflight.fontTooLarge',
        ns: 'errors',
        values: { effective: '12.00', max: '12' },
      },
    })
    expect(issueValues(tooLarge).expected).toBe('≤ 12pt（当前值实际略高，显示已四舍五入）')
  })

  it('设置页问的是同一张表：填 8 时两条下限的要求不是同一句话', () => {
    expect(ruleExpectation('font-below-absolute-floor', 8)).toBe('大于 8pt')
    expect(ruleExpectation('font-too-small', 8)).toBe('≥ 8pt')
    expect(ruleExpectation('min_raster_dpi-not-a-rule', 8)).toBeNull()
    // 没登记比较词的规则不硬造一句
    expect(ruleExpectation('page-width', 80)).toBeNull()
  })
})
