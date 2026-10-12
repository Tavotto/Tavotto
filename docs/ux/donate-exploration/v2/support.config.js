/*
 * Tavotto support page — the one place for everything that must be configured or confirmed.
 *
 * Nothing in here is guessed. Empty values are rendered honestly as "not configured" in preview,
 * and `node check-config.mjs` refuses to pass until each item below is real and confirmed.
 */
window.TAVOTTO_SUPPORT_CONFIG = {
  /* true while the page is a design preview: shows the dashed "not configured / draft" markers.
     Must be false for a public launch (check-config enforces it). */
  preview: true,

  /* The project's own Ko-fi page, e.g. "https://ko-fi.com/<account>". Leave empty until the
     maintainer supplies it — never guess an account name. Empty = the button is inert. */
  kofiUrl: '',

  /* A note from the maintainer. The text is a DRAFT written for the maintainer to confirm or
     rewrite; nothing in it states facts about a person. `noteConfirmed` must be set by them. */
  maintainer: {
    name: '',                                   /* display name exactly as they want it printed */
    role: { en: 'Maintainer, Tavotto', zh: 'Tavotto 维护者' },
    noteConfirmed: false
  },

  /* "Where your support goes": wording proposed by design, to be confirmed by the maintainer. */
  fundingConfirmed: false,

  /* How a supporter changes their listed name, becomes anonymous or leaves the list.
     A mailto: or https: address the project actually reads. */
  acknowledgementsContact: '',

  /* Verifiable recent work: each item quotes a published release note in this repository. */
  recentWork: [
    {
      version: '0.18',
      href: 'https://github.com/Tavotto/Tavotto/blob/main/docs/release-notes/v0.18.0.md',
      en: 'Saving writes the layout into your project, and a project file changed by another program is never silently overwritten.',
      zh: '保存会把版面写进你的项目；项目文件若被其他程序改动过，Tavotto 会请你处理冲突，而不是悄悄覆盖。'
    },
    {
      version: '0.17',
      href: 'https://github.com/Tavotto/Tavotto/blob/main/docs/release-notes/v0.17.0.md',
      en: 'Rendering and export moved to RenderCore; mirrored and semi-transparent panels now stay vector; the first desktop app for Intel Macs.',
      zh: '渲染与导出改由 RenderCore 完成；镜像与半透明面板保持矢量；首次提供 Intel Mac 桌面版。'
    },
    {
      version: '0.16',
      href: 'https://github.com/Tavotto/Tavotto/blob/main/docs/release-notes/v0.16.0.md',
      en: 'Missing packages are prepared before the first run, all at once, and every exported file is reopened and checked.',
      zh: '首次运行前一次性准备好缺失的依赖包；每一个导出的文件都会被重新打开并检查。'
    }
  ],

  links: {
    repo: 'https://github.com/Tavotto/Tavotto',
    issues: 'https://github.com/Tavotto/Tavotto/issues',
    releases: 'https://github.com/Tavotto/Tavotto/releases',
    privacy: 'https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md'
  }
};
