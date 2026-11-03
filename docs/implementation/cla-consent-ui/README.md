# CLA consent wording versions

Every signature stores `consent_ui_version` (`services/cla-cloudflare/src/native/service.ts`, `CONSENT_UI_VERSION`). This file records the exact wording shown beside each attestation for each version, so any stored signature can be matched to what its signer saw. Versions are append-only; the service keeps every version readable (`CONSENT_UI_VERSIONS`).

The attestation **keys** never change; contributors confirm `personal`, `electronic`, `read`, `identity`, `adult`, `own_rights`, `independent`; the rights holder confirms `personal`, `electronic`, `read`, `identity`, `reviewed`.

## Version 1 (until 2026-11)

Chinese-only signing page.

| Key | Wording (zh) |
|---|---|
| `personal` | 我本人作出此决定；没有由他人代签。 |
| `electronic` | 我同意用电子签名作出本次签署。 |
| `read` | 我已经阅读上方完整协议，并同意这个版本和哈希对应的文本。 |
| `identity` | 该 GitHub 数字身份属于我；姓名和地址是我本人填写的。 |
| `adult` | 我已满十八岁。 |
| `own_rights` | 我拥有本次贡献所需的权利；不属于他人或雇主所有。 |
| `independent` | 我以个人身份签署；若涉及雇主或公司权利，我会先寻求人工审查。 |
| `reviewed` | 我作为权利人已独立审阅此协议及贡献者的签署证据，并愿意签署。 |

## Version 2 (2026-11 redesign)

Bilingual page: English by default, Chinese when the browser prefers it or the signer switches. The Chinese wording is version 1's, unchanged; the English wording is new. A test (`tests/consent-wording.test.mjs`) keeps this table equal to `src/native/ui-client.ts`.

| Key | Wording (en) | Wording (zh) |
|---|---|---|
| `personal` | I am making this decision myself; no one is signing for me. | 我本人作出此决定；没有由他人代签。 |
| `electronic` | I agree to sign electronically. | 我同意用电子签名作出本次签署。 |
| `read` | I have read the whole agreement above, and I agree to this exact version and fingerprint. | 我已经阅读上方完整协议，并同意这个版本和哈希对应的文本。 |
| `identity` | This GitHub account is mine, and I entered my own name and address. | 该 GitHub 数字身份属于我；姓名和地址是我本人填写的。 |
| `adult` | I am 18 or older. | 我已满十八岁。 |
| `own_rights` | I hold the rights in my contributions; they do not belong to someone else or an employer. | 我拥有本次贡献所需的权利；不属于他人或雇主所有。 |
| `independent` | I am signing as an individual. If an employer or company has rights, I will ask for review first. | 我以个人身份签署；若涉及雇主或公司权利，我会先寻求人工审查。 |
| `reviewed` | As the rights holder, I have independently reviewed this agreement and the contributor’s signature, and I want to sign. | 我作为权利人已独立审阅此协议及贡献者的签署证据，并愿意签署。 |
