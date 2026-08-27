# Tavotto Corporate (Entity) Contributor License Agreement

**CLA_VERSION: 1.0**

> This document is an engineering/licensing artefact, not legal advice. Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel before this agreement is relied on in a transaction.

## When this agreement applies instead of the individual one

Use this agreement, not the [Individual CLA](CLA_INDIVIDUAL.md), when the
contribution is owned by a company rather than by the person typing it. In most
employment relationships the employer owns work created in the course of
employment, so this is the normal case for a contribution made on company time,
on company equipment, or within the scope of someone's job.

An individual cannot fix this by signing the Individual CLA: Section 3(b) of
that agreement asks the signer to confirm that *they* own the copyright, which
is not true when the employer does.

## Provenance of this text

This agreement is derived from the **Harmony Entity Contributor License
Agreement, version 1.0** (Project Harmony, released 4 July 2011), retrieved from
<https://www.harmonyagreements.org/docs/ha-combined-v1> on 2026-08-27.

The Harmony combined template carries the notice *"This work is licensed under a
Creative Commons Attribution 3.0 Unported License"*, and the Harmony policies
page grants "a worldwide, non-exclusive, royalty free license to any party
('Licensee') to modify, reproduce and distribute the Template Agreements for the
licensing of copyrightable works." This document is distributed under those
terms, with attribution given above.

**Project Harmony trademarks are deliberately not used**, for the reason given in
[CLA_INDIVIDUAL.md](CLA_INDIVIDUAL.md#provenance-of-this-text).

### What was configured, and what was changed

| Harmony element | Choice made for Tavotto |
|---|---|
| Base agreement | **Contributor License Agreement (CLA)**, not Contributor Assignment Agreement (CAA). The Entity keeps copyright. |
| Variant | **Entity**. The Individual variant is [CLA_INDIVIDUAL.md](CLA_INDIVIDUAL.md). |
| Section 2.1 | The **Copyright License** form (`2.1(a)`/`(b)`). |
| Section 2.3 outbound licence | **Option Five**, identical to the Individual CLA. |
| Media licence sentence | **Omitted**, identical to the Individual CLA. |
| `[or Your Affiliates]` brackets | **Retained** — this is the Entity agreement, where Affiliates are defined. |
| `[assigned or]` in 2.6 | **Removed** — nothing is assigned under the CLA form. |
| Section 3(c) | The **(Entity)** form. |
| `[AND BY US TO YOU]` / `[OR US]` | **Retained** in Sections 4 and 5. |
| `[PROJECT_NAME]` | `Tavotto` |
| `[SUBMISSION_INSTRUCTIONS]` | See [How to sign](#how-to-sign); delivery to <support@tavotto.com>. |
| `[NONOWNER_INSTRUCTIONS]` | See [Schedule A](#schedule-a--authorised-contributors). |
| `[JURISDICTION]` | Hong Kong SAR, China. |

**One addition beyond the template.** Harmony 1.0 has no schedule of covered
people; it binds the Entity as a whole. Tavotto needs to know *which GitHub
accounts* a corporate signature covers, or the CI check cannot tell an
authorised employee from an unrelated account. [Schedule A](#schedule-a--authorised-contributors)
is therefore appended as an **administrative annex**. It is deliberately placed
*after* the operative text and grants no rights of its own — it records scope and
points of contact for the agreement, and Section 2 is unchanged by it.

## The counterparty

Identical to the Individual CLA: **"We"/"Us" is Jiaqi Wan**, a natural person,
and the governing law is that of the Hong Kong Special Administrative Region
(Section 6.1). Contact: <support@tavotto.com>. The postal address is completed on the
executed copy rather than published here. See
[CLA_INDIVIDUAL.md](CLA_INDIVIDUAL.md#the-counterparty).

## How to sign

**Corporate signatures are handled by manual legal review, and this is a
deliberate design decision rather than a missing feature.**

No CI check can safely establish that a given GitHub username is authorised to
bind a company. The evidence that matters — that the signer holds signing
authority, that the listed accounts really are employees, that the scope of
authorisation is what it claims — lives entirely outside GitHub. Automating a
judgement about corporate authority would produce a check that looks
authoritative and verifies nothing.

The intended flow, once the rights holder is configured:

1. The company's authorised signatory completes this agreement, including
   Schedule A.
2. It is sent to <support@tavotto.com> for review by a human. Postal delivery details, if
   needed, are exchanged from there — so that no personal address has to be
   published in the repository.
3. On acceptance, the covered accounts from Schedule A are registered with the
   signature provider, against the agreement version that was signed.
4. From that point the provider's check recognises those accounts, and the
   repository's gate follows it.

The repository itself stores no record of who signed — that lives with the
provider (see [CLA_VERSIONING.md](CLA_VERSIONING.md#where-signature-records-live)).
Step 3 is the only point where automation is involved, and it merely reflects a
decision a human already made.

---

# Agreement

Thank you for your interest in contributing to Tavotto ("We" or "Us").

This contributor agreement ("Agreement") documents the rights granted by
contributors to Us. To make this document effective, please sign it and send it
to Us by mail, email, fax, or electronic submission, following the instructions
at [How to sign](#how-to-sign). This is a legally binding document, so please
read it carefully before agreeing to it. The Agreement may cover more than one
software project managed by Us.

## 1. Definitions

**"You"** means any Legal Entity on behalf of whom a Contribution has been
received by Us. **"Legal Entity"** means an entity which is not a natural
person. **"Affiliates"** means other Legal Entities that control, are controlled
by, or under common control with that Legal Entity. For the purposes of this
definition, "control" means (i) the power, direct or indirect, to cause the
direction or management of such Legal Entity, whether by contract or otherwise,
(ii) ownership of fifty percent (50%) or more of the outstanding shares or
securities which vote to elect the management or other persons who direct such
Legal Entity or (iii) beneficial ownership of such entity.

**"Contribution"** means any work of authorship that is Submitted by You to Us
in which You own or assert ownership of the Copyright. If You do not own the
Copyright in the entire work of authorship, please follow the instructions in
[Schedule A](#schedule-a--authorised-contributors).

**"Copyright"** means all rights protecting works of authorship owned or
controlled by You or Your Affiliates, including copyright, moral and neighboring
rights, as appropriate, for the full term of their existence including any
extensions by You.

**"Material"** means the work of authorship which is made available by Us to
third parties. When this Agreement covers more than one software project, the
Material means the work of authorship to which the Contribution was Submitted.
After You Submit the Contribution, it may be included in the Material.

**"Submit"** means any form of electronic, verbal, or written communication sent
to Us or our representatives, including but not limited to electronic mailing
lists, source code control systems, and issue tracking systems that are managed
by, or on behalf of, Us for the purpose of discussing and improving the
Material, but excluding communication that is conspicuously marked or otherwise
designated in writing by You as "Not a Contribution."

**"Submission Date"** means the date on which You Submit a Contribution to Us.

**"Effective Date"** means the date You execute this Agreement or the date You
first Submit a Contribution to Us, whichever is earlier.

**"Media"** means any portion of a Contribution which is not software.

## 2. Grant of Rights

### 2.1 Copyright License

(a) You retain ownership of the Copyright in Your Contribution and have the same
rights to use or license the Contribution which You would have had without
entering into the Agreement.

(b) To the maximum extent permitted by the relevant law, You grant to Us a
perpetual, worldwide, non-exclusive, transferable, royalty-free, irrevocable
license under the Copyright covering the Contribution, with the right to
sublicense such rights through multiple tiers of sublicensees, to reproduce,
modify, display, perform and distribute the Contribution as part of the
Material; provided that this license is conditioned upon compliance with
Section 2.3.

### 2.2 Patent License

For patent claims including, without limitation, method, process, and apparatus
claims which You or Your Affiliates own, control or have the right to grant, now
or in the future, You grant to Us a perpetual, worldwide, non-exclusive,
transferable, royalty-free, irrevocable patent license, with the right to
sublicense these rights to multiple tiers of sublicensees, to make, have made,
use, sell, offer for sale, import and otherwise transfer the Contribution and
the Contribution in combination with the Material (and portions of such
combination). This license is granted only to the extent that the exercise of
the licensed rights infringes such patent claims; and provided that this license
is conditioned upon compliance with Section 2.3.

### 2.3 Outbound License

Based on the grant of rights in Sections 2.1 and 2.2, if We include Your
Contribution in a Material, We may license the Contribution under any license,
including copyleft, permissive, commercial, or proprietary licenses. As a
condition on the exercise of this right, We agree to also license the
Contribution under the terms of the license or licenses which We are using for
the Material on the Submission Date.

### 2.4 Moral Rights

If moral rights apply to the Contribution, to the maximum extent permitted by
law, You waive and agree not to assert such moral rights against Us or our
successors in interest, or any of our licensees, either direct or indirect.

### 2.5 Our Rights

You acknowledge that We are not obligated to use Your Contribution as part of
the Material and may decide to include any Contribution We consider appropriate.

### 2.6 Reservation of Rights

Any rights not expressly licensed under this section are expressly reserved by
You.

## 3. Agreement

You confirm that:

(a) You have the legal authority to enter into this Agreement.

(b) You or Your Affiliates own the Copyright and patent claims covering the
Contribution which are required to grant the rights under Section 2.

(c) The grant of rights under Section 2 does not violate any grant of rights
which You or Your Affiliates have made to third parties.

(d) You have followed the instructions in
[Schedule A](#schedule-a--authorised-contributors), if You do not own the
Copyright in the entire work of authorship Submitted.

## 4. Disclaimer

EXCEPT FOR THE EXPRESS WARRANTIES IN SECTION 3, THE CONTRIBUTION IS PROVIDED "AS
IS". MORE PARTICULARLY, ALL EXPRESS OR IMPLIED WARRANTIES INCLUDING, WITHOUT
LIMITATION, ANY IMPLIED WARRANTY OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NON-INFRINGEMENT ARE EXPRESSLY DISCLAIMED BY YOU TO US AND BY US TO
YOU. TO THE EXTENT THAT ANY SUCH WARRANTIES CANNOT BE DISCLAIMED, SUCH WARRANTY
IS LIMITED IN DURATION TO THE MINIMUM PERIOD PERMITTED BY LAW.

## 5. Consequential Damage Waiver

TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT WILL YOU OR US BE
LIABLE FOR ANY LOSS OF PROFITS, LOSS OF ANTICIPATED SAVINGS, LOSS OF DATA,
INDIRECT, SPECIAL, INCIDENTAL, CONSEQUENTIAL AND EXEMPLARY DAMAGES ARISING OUT
OF THIS AGREEMENT REGARDLESS OF THE LEGAL OR EQUITABLE THEORY (CONTRACT, TORT OR
OTHERWISE) UPON WHICH THE CLAIM IS BASED.

## 6. Miscellaneous

6.1 This Agreement will be governed by and construed in accordance with the laws
of the Hong Kong Special Administrative Region of the People's Republic of China
excluding its conflicts of law provisions. Under certain circumstances, the governing law in this section might
be superseded by the United Nations Convention on Contracts for the
International Sale of Goods ("UN Convention") and the parties intend to avoid
the application of the UN Convention to this Agreement and, thus, exclude the
application of the UN Convention in its entirety to this Agreement.

6.2 This Agreement sets out the entire agreement between You and Us for Your
Contributions to Us and overrides all other agreements or understandings.

6.3 If You or We assign the rights or obligations received through this
Agreement to a third party, as a condition of the assignment, that third party
must agree in writing to abide by all the rights and obligations in the
Agreement.

6.4 The failure of either party to require performance by the other party of any
provision of this Agreement in one situation shall not affect the right of a
party to require such performance at any time in the future. A waiver of
performance under a provision in one situation shall not be considered a waiver
of the performance of the provision in the future or a waiver of the provision
in its entirety.

6.5 If any provision of this Agreement is found void and unenforceable, such
provision will be replaced to the extent possible with a provision that comes
closest to the meaning of the original provision and which is enforceable. The
terms and conditions set forth in this Agreement shall apply notwithstanding any
failure of essential purpose of this Agreement or any limited remedy to the
maximum extent possible under law.

---

**You (Legal Entity)**

```
Entity name:  ________________________
Signature:    ________________________
Name:         ________________________
Title:        ________________________
Address:      ________________________
              ________________________
Date:         ________________________
```

**Us**

```
Signature: ________________________
Name:      Jiaqi Wan
Title:     ________________________
Address:   ________________________
Date:      ________________________
```

---

## Schedule A — authorised contributors

*Administrative annex. Not part of the operative agreement above; it records
scope and contacts and grants no rights of its own.*

**Point of contact at the Entity** — who We contact about this agreement:

```
Name:   ________________________
Email:  ________________________
```

Send the completed agreement to <support@tavotto.com>.

**Accounts covered by this agreement.** List every GitHub account authorised to
Submit Contributions on behalf of the Entity. Contributions from accounts not
listed here are not covered.

| GitHub account | Person's name | Role |
|---|---|---|
| | | |
| | | |

**Keeping this list current.** Send an updated Schedule A to the point of
contact above when someone joins or leaves. Removing an account ends its
coverage for *future* Contributions only — rights already granted in
Contributions that were Submitted while the account was listed are perpetual and
irrevocable under Section 2.1(b), and are not affected.

**Partly-owned work.** If a Contribution contains material the Entity does not
own — third-party code, or work of a contractor whose agreement did not transfer
copyright — do not Submit it under this agreement. Raise it with the point of
contact first.

---

*Derived from the Harmony Entity Contributor License Agreement, version 1.0,
© 2011 Project Harmony, licensed under
[CC BY 3.0 Unported](https://creativecommons.org/licenses/by/3.0/).*
