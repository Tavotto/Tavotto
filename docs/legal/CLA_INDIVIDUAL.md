# Tavotto Individual Contributor License Agreement

**CLA_VERSION: 1.0-draft**

> This document is an engineering/licensing artefact, not legal advice. It is
> **not yet in force**: the counterparty ("We"/"Us") and the governing law are
> still unset — see [Configuration required](#configuration-required). Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel.

## Provenance of this text

This agreement is derived from the **Harmony Individual Contributor License
Agreement, version 1.0** (Project Harmony, released 4 July 2011), retrieved from
<https://www.harmonyagreements.org/docs/ha-combined-v1> on 2026-08-27.

The Harmony combined template carries the notice *"This work is licensed under a
Creative Commons Attribution 3.0 Unported License"*, and the Harmony policies
page grants "a worldwide, non-exclusive, royalty free license to any party
('Licensee') to modify, reproduce and distribute the Template Agreements for the
licensing of copyrightable works." This document is distributed under those
terms, with attribution given above.

**Project Harmony trademarks are deliberately not used.** The Harmony trademark
licence covers only the *unmodified* template; because this document selects
options and fills in project-specific blanks, that trademark licence does not
apply and is not relied on. The reference above is a factual statement of
derivation, not a claim of endorsement or certification by Project Harmony.

### What was configured, and what was changed

Everything in [Agreement](#agreement) below is the Harmony 1.0 operative text
with the template's own choices resolved. The record of those choices:

| Harmony element | Choice made for Tavotto |
|---|---|
| Base agreement | **Contributor License Agreement (CLA)**, not Contributor Assignment Agreement (CAA). The contributor keeps copyright. |
| Variant | **Individual**. The Entity variant is [CLA_CORPORATE.md](CLA_CORPORATE.md). |
| Section 2.1 | The **Copyright License** form (`2.1(a)`/`(b)`). The three-part *Copyright Assignment* form was not used. |
| Section 2.3 outbound licence | **Option Five** — the only option permitting separately licensed editions, while binding Us to keep licensing the Contribution under the licence in use on the Submission Date. |
| Media licence sentence | **Omitted.** The template presents an optional additional Media licence list; Tavotto does not license Media separately. |
| `[or Your Affiliates]` brackets | **Removed** — Affiliates are an Entity-agreement concept. |
| `[assigned or]` in 2.6 | **Removed** — nothing is assigned under the CLA form. |
| Section 3(c) | The **(Individual)** form. |
| `[AND BY US TO YOU]` / `[OR US]` | **Retained** in Sections 4 and 5, making the disclaimer and damages waiver mutual. |
| `[PROJECT_NAME]` | `Tavotto` |
| `[SUBMISSION_INSTRUCTIONS]` | See [How to sign](#how-to-sign). |
| `[NONOWNER_INSTRUCTIONS]` | See [If you do not own the whole contribution](#if-you-do-not-own-the-whole-contribution). |
| `[JURISDICTION]` | **`RIGHTS_HOLDER_CONFIGURATION_REQUIRED`** — unresolved. |

No other wording in the operative sections was altered. Section headings were
renumbered for Markdown only.

## Configuration required

Two blanks are unresolved, and the agreement cannot be executed until they are
filled in by the rights holder:

- **`RIGHTS_HOLDER_CONFIGURATION_REQUIRED` — the identity of "We"/"Us".** The
  repository does not currently record a legal contracting entity. `README.md`
  says only "Tavotto™ is a trademark of the Tavotto project", `pyproject.toml`
  names `erwanjun` as author, and no company, foundation or incorporated body
  appears anywhere in the tree. A GitHub organisation is not a legal person.
- **`RIGHTS_HOLDER_CONFIGURATION_REQUIRED` — the governing law (Section 6.1).**

Until both are set and the version is moved off `-draft`, this document is a
prepared template, not an operative contract. See
[CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md).

## How to sign

The signing flow is not yet enabled — no signature provider is installed. See
[CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md) for the remaining steps and
[CLA_VERSIONING.md](CLA_VERSIONING.md) for how a signature is bound to a
specific version of this text.

## If you do not own the whole contribution

If any part of what you are submitting was written by someone else, or belongs
to your employer, do not submit it under this agreement. Open an issue first and
say so. Depending on the case the right route is the
[Corporate CLA](CLA_CORPORATE.md), a separate agreement from the other author,
or leaving that code out.

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

**"You"** means the individual who Submits a Contribution to Us.

**"Contribution"** means any work of authorship that is Submitted by You to Us
in which You own or assert ownership of the Copyright. If You do not own the
Copyright in the entire work of authorship, please follow the instructions in
[If you do not own the whole contribution](#if-you-do-not-own-the-whole-contribution).

**"Copyright"** means all rights protecting works of authorship owned or
controlled by You, including copyright, moral and neighboring rights, as
appropriate, for the full term of their existence including any extensions by
You.

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
claims which You own, control or have the right to grant, now or in the future,
You grant to Us a perpetual, worldwide, non-exclusive, transferable,
royalty-free, irrevocable patent license, with the right to sublicense these
rights to multiple tiers of sublicensees, to make, have made, use, sell, offer
for sale, import and otherwise transfer the Contribution and the Contribution in
combination with the Material (and portions of such combination). This license
is granted only to the extent that the exercise of the licensed rights infringes
such patent claims; and provided that this license is conditioned upon
compliance with Section 2.3.

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

(b) You own the Copyright and patent claims covering the Contribution which are
required to grant the rights under Section 2.

(c) The grant of rights under Section 2 does not violate any grant of rights
which You have made to third parties, including Your employer. If You are an
employee, You have had Your employer approve this Agreement or sign the Entity
version of this document. If You are less than eighteen years old, please have
Your parents or guardian sign the Agreement.

(d) You have followed the instructions in
[If you do not own the whole contribution](#if-you-do-not-own-the-whole-contribution),
if You do not own the Copyright in the entire work of authorship Submitted.

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
of `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` excluding its conflicts of law
provisions. Under certain circumstances, the governing law in this section might
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

**You (Individual)**

```
Signature: ________________________
Name:      ________________________
Address:   ________________________
           ________________________
Date:      ________________________
GitHub:    ________________________
```

**Us**

```
Signature: ________________________
Name:      ________________________   (RIGHTS_HOLDER_CONFIGURATION_REQUIRED)
Title:     ________________________
Address:   ________________________
Date:      ________________________
```

---

*Derived from the Harmony Individual Contributor License Agreement, version 1.0,
© 2011 Project Harmony, licensed under
[CC BY 3.0 Unported](https://creativecommons.org/licenses/by/3.0/).*
