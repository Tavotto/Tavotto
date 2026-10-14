/**
 * 维护者的企业微信联系方式（仅中文界面使用，见 `components/settings/WeComContact.tsx`）。
 * 界面只从这里取两个地址：获客链接（点了用系统浏览器开）与名片码解码出的地址（二维码图片
 * `assets/wecom-contact-qr.png` 必须解码为它，`tests/test_wecom_contact_qr.py` 钉住）。
 */
export const WECOM_CONTACT_URL = 'https://work.weixin.qq.com/ca/cawcdeab64ef98a3e9'
export const WECOM_CARD_URL = 'https://work.weixin.qq.com/u/vc56c00d0f7cb8eae0?src=wx&bb=9c371d3ab1'
