import type { Page } from '@playwright/test'

/** axe 报的一个节点：`target` 是它生成的选择器，`html` 是**扫描那一刻**的 outerHTML 片段 */
export interface ScannedNode {
  target: string
  html: string
}

/**
 * 把 axe 报的节点在页面里重新找到，并把各条 allowance 要看的事实一次读回来。
 *
 * **主语按身份钉住，不按字符串重解析**（2026-09-15，#354 / #356 的 Windows 腿）：axe 扫描
 * 在 webkit 上要几秒，它交回来的只是选择器字符串；等 verify 跑的时候 DOM 可能已经变了——
 * 面板角标退场、导出对话框的校验结果到达——同一个 `.py-0\.5` 就从「角标」指到了「阻断
 * 清单的 li」，后者没有直接文字，判据如实报「扫不到」，可它根本不是 axe 报的那个节点。
 * 所以解析出元素之后先用 axe 自带的 `html` 核对身份：对不上就按「扫描之后已经消失」处理
 * （与 `!el` 同一个语义），不拿别人的身子去判 axe 说的事。
 *
 * 身份只比 tagName、id、class 与 data-*（`data-state` 除外：它随开合变，不是身份）；
 * axe 对长元素会把属性值截成 `xx...`、开标签截成 ` ...>`，所以比的是前缀。
 */
export async function resolveScanned(page: Page, nodes: ScannedNode[]): Promise<ScannedFacts[]> {
  return page.evaluate((list) => {
    const openingTag = (html: string): string => {
      // 第一个不在引号里的 `>` 之前就是开标签（属性值里可能有 `>`）
      let quote = ''
      for (let i = 0; i < html.length; i++) {
        const ch = html[i]
        if (quote) {
          if (ch === quote) quote = ''
        } else if (ch === '"' || ch === "'") quote = ch
        else if (ch === '>') return html.slice(0, i + 1)
      }
      return html
    }
    const sameAsScanned = (el: Element, html: string): boolean => {
      const open = openingTag(html)
      const tag = /^<([a-zA-Z0-9-]+)/.exec(open)
      if (!tag || tag[1].toLowerCase() !== el.tagName.toLowerCase()) return false
      const attrRe = /\s([^\s=]+)="([^"]*)"/g
      let m: RegExpExecArray | null
      while ((m = attrRe.exec(open))) {
        const [, name, raw] = m
        if (name.endsWith('...')) continue
        // 完整 outerHTML 形态里属性值是序列化转义过的（& " nbsp）；截断形态是原值，
        // 原值里出现这三个序列的概率可以忽略，所以两种形态统一解码
        const value = raw.replace(/&quot;/g, '"').replace(/&nbsp;/g, '\u00a0').replace(/&amp;/g, '&')
        if (name !== 'id' && name !== 'class' && !name.startsWith('data-')) continue
        if (name === 'data-state') continue
        const actual = el.getAttribute(name)
        if (actual == null) return false
        if (value.endsWith('...')) {
          if (!actual.startsWith(value.slice(0, -3))) return false
        } else if (actual !== value) return false
      }
      return true
    }
    return list.map(({ target, html }) => {
      let el: Element | null = null
      try {
        el = document.querySelector(target)
      } catch {
        return { target, found: 'unparseable' as const }
      }
      if (!el) return { target, found: 'gone' as const }
      if (html && !sameAsScanned(el, html)) return { target, found: 'other' as const }
      const direct = [...el.childNodes]
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent ?? '')
        .join('')
        .trim()
      return {
        target,
        found: 'same' as const,
        direct,
        disabled: el.closest('[disabled], [aria-disabled="true"], fieldset[disabled]') != null,
        guard: el.hasAttribute('data-radix-focus-guard'),
        hidden: el.closest('[aria-hidden="true"], [data-aria-hidden="true"]') != null,
        inDialog: el.closest('[role="dialog"]') != null,
      }
    })
  }, nodes)
}

export type ScannedFacts =
  | { target: string; found: 'unparseable' | 'gone' | 'other' }
  | {
      target: string
      found: 'same'
      direct: string
      disabled: boolean
      guard: boolean
      hidden: boolean
      inDialog: boolean
    }
