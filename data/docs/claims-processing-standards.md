# Claims Processing Standards

**Document ID:** CH-CL-007
**Effective date:** 1 January 2025
**Owner:** Claims Operations

> Fictional document created for a software demonstration.

---

## 1. Timely filing

**A claim must be received by Cascade within 365 calendar days of the date of service.** The receipt date, not the submission date, controls — except where the provider can produce a clearinghouse acceptance report, in which case the acceptance timestamp controls.

For claims where Cascade is the **secondary** payer, the window is 365 days from the date of the primary payer's Explanation of Benefits, not from the date of service.

Claims received outside the window are denied **DN-TIMELY**. A DN-TIMELY denial may not be billed to the member: it is a provider administrative failure, and the member is held harmless.

### Corrected and replacement claims
A corrected claim must be received within **90 calendar days of the original adjudication date**, or within the original 365-day filing window, whichever is later. Corrected claims must be marked as replacements and reference the original claim identifier; a corrected claim submitted as a new claim is denied **DN-DUPLICATE**.

## 2. What makes a claim "clean"

A clean claim contains everything needed to adjudicate it without further information. It requires:

- A member identifier that matches an enrolment record active on the date of service
- A valid provider identifier
- Date of service, and for facility claims an admission and discharge date
- At least one valid procedure code, with modifiers where applicable
- At least one valid diagnosis code, consistent with the procedure billed
- A place-of-service code
- Billed charges for every line
- Units for every line
- For a service on the prior authorization list, a reference to an approved authorization
- Where another payer is primary, that payer's Explanation of Benefits

A claim missing any of these is not clean, and the processing clocks in Section 3 do not begin until the missing element is received.

## 3. Adjudication turnaround targets

| Claim type | Target | Regulatory maximum |
| --- | --- | --- |
| Clean electronic professional claim | 14 calendar days | 30 calendar days |
| Clean electronic facility claim | 21 calendar days | 30 calendar days |
| Clean paper claim | 30 calendar days | 45 calendar days |
| Claim requiring clinical review | 30 calendar days | 45 calendar days |
| Claim pended for coordination of benefits | 45 calendar days | 60 calendar days |

Turnaround is measured from **received_date to paid_date**. A claim in `pending` status has not completed adjudication and has no paid date.

Claims Operations reports the 90th percentile of turnaround, not the mean. A mean turnaround inside target while the 90th percentile sits outside it means a minority of members are waiting a very long time, and that is the number that generates complaints.

### Interest on late payment
A clean claim not adjudicated within its regulatory maximum accrues interest at 12% simple annual interest from the day after the maximum expires until the payment date. Interest is paid automatically and does not require a provider request.

## 4. Adjudication sequence

Every claim passes through these checks in order. The **first** check that fails determines the denial code, which is why a claim with several problems shows only one reason.

1. **Duplicate detection** — matches on member, provider, date of service, procedure code and billed amount. Failure → `DN-DUPLICATE`
2. **Eligibility** — was the member enrolled on the date of service? Failure → `DN-ELIGIBILITY`
3. **Timely filing** — was the claim received inside the window? Failure → `DN-TIMELY`
4. **Code validity** — are the procedure and diagnosis codes valid, correctly bundled, and consistent with each other and with the place of service? Failure → `DN-CODING`
5. **Benefit coverage** — is the service a covered benefit under this member's plan? Failure → `DN-NOT-COVERED`
6. **Authorization** — for services on the prior authorization list, is an approved authorization on file? Failure → `DN-PRIOR-AUTH`
7. **Referral** — for specialist services on plans requiring referrals, is a referral on file? Failure → `DN-REFERRAL`
8. **Medical necessity** — does the service meet clinical criteria? Failure → `DN-MED-NEC`
9. **Network** — is the provider contracted, and if not does an exception apply? Failure → `DN-OON`
10. **Pricing** — apply the contracted or plan fee schedule to determine the allowed amount
11. **Benefit application** — apply deductible, copay and coinsurance to determine the member's share and the plan's payment

Because duplicate and eligibility checks run before code validity, a claim with both an invalid code and an eligibility problem is always denied `DN-ELIGIBILITY`. Analysts reading denial mix should keep this ordering in mind: the reported denial reason is the first failure, not the only one.

## 5. Pricing and the allowed amount

The allowed amount is determined as follows:

- **In-network:** the contracted rate for the procedure code, by provider contract.
- **Out-of-network, non-emergency:** the plan's out-of-network fee schedule. The provider is not bound by it and may balance-bill the member for the difference.
- **Out-of-network, emergency:** priced as though in-network, and the member's cost share is calculated at the in-network level. Balance billing for emergency services is prohibited; see CH-NW-003 Section 6.
- **Facility claims:** priced by the applicable case rate or per-diem where the contract specifies one, otherwise by the fee schedule.

After pricing, the plan's payment and the member's share always satisfy one of these identities for a paid claim:

- In-network: `plan_paid + member_responsibility = allowed_amount`
- Out-of-network: `plan_paid + member_responsibility = billed_amount`, because the unwritten-off balance falls to the member

Denied and pending claims have an allowed amount and plan payment of zero. **Any spend analysis must filter to paid claims before summing**, or it will silently include zero-value denials in its denominator.

## 6. Member liability on denied claims

Whether a member can be billed for a denied claim depends on the reason:

| Denial | Member may be billed? |
| --- | --- |
| DN-NOT-COVERED | Yes — the service is not a plan benefit |
| DN-OON | Yes — subject to the emergency-care protections in CH-NW-003 |
| DN-FORMULARY | Yes |
| DN-PRIOR-AUTH | **No** — provider administrative failure |
| DN-REFERRAL | **No** — provider administrative failure |
| DN-TIMELY | **No** — provider administrative failure |
| DN-DUPLICATE | **No** — nothing additional is owed |
| DN-CODING | **No** — the provider must correct and resubmit |
| DN-MED-NEC | Only after the member has been given written notice in advance and has accepted financial responsibility |
| DN-ELIGIBILITY | Yes, if the member genuinely was not enrolled |

## 7. Overpayment recovery

Cascade may recover an overpayment within **24 months** of the original payment date, extended to 60 months where fraud, waste or abuse is documented. Recovery notices state the claim, the amount, and the basis. Providers have 30 calendar days to dispute before offset against future payments begins.

## 8. Reporting definitions

These definitions are fixed so that figures reconcile between reports:

- **Denial rate** = denied claims ÷ all adjudicated claims (paid + denied). Pending claims are excluded from both numerator and denominator, because they have not been adjudicated.
- **Plan spend** = sum of `plan_paid` for claims with status `paid`.
- **Member cost share** = sum of `member_responsibility` for claims with status `paid`.
- **Utilisation period** = grouped on `service_date`.
- **Cash period** = grouped on `paid_date`.
- **Turnaround** = `paid_date − received_date`, in calendar days, for adjudicated claims only.

Reports that group spend by `paid_date` and utilisation by `service_date` are both correct, and they will not agree with each other. State which basis a figure uses.
