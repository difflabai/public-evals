# Bluestem CRM Field Definitions & Population Counts (as of 2026-05-31)

**System:** BlueTrack CRM, synced nightly from the order management system (OMS)
**Maintained by:** Sales Operations — Tyler Brandt, Sales Analyst (field owner)
**Review cadence:** Quarterly. Last full review: 2026-05-31.
**Distribution:** Sales leadership, Finance, Marketing. Please route definition-change requests through Sales Ops before quoting CRM counts in external materials.

## 1. Purpose

This dictionary is the controlling reference for how account-level fields in BlueTrack are defined and calculated. Several fields look self-explanatory and are not. When a number pulled from the CRM disagrees with a number published by Finance, the first step is to check the field definition below — most disputes are definitional, not data errors.

## 2. Field definitions

| Field | Definition | Notes |
|---|---|---|
| account_id | Unique customer key, format BL-#####. | Assigned at account creation; never reused. |
| account_name | Legal or trade name of the operating location. | Multi-unit chains carry one record per contracting entity, not per unit. |
| segment | STR = street/independent restaurants; CHN = regional chains; EDU = K-12 and college dining; HCF = healthcare foodservice; EQP = equipment & supplies only. | Segment is set by account type at onboarding and reviewed annually. |
| status | **active** = at least one invoiced order in the trailing 365 days from the reporting date; **dormant** = no invoiced order in the trailing 365 days. | Recalculated nightly. Dormant records are retained for win-back targeting and are NOT deleted. |
| last_order_date | Invoice date of the most recent order. | Drives the status calculation. |
| portal_enrolled | Y = account has completed Bluestem Direct onboarding and holds live credentials. | Enrollment is not the same as usage; enrolled accounts still place some orders through their rep. |
| lapse_90d_flag | set when an account records no order for 90 consecutive days; NOTE: this is a re-engagement trigger, not an attrition metric. | Feeds the inside-sales re-engagement call queue. Clears on the next invoiced order. |
| fy2025_revenue_usd | Invoiced net revenue, calendar FY2025. | Ties to the Finance segment ledger at fiscal close. |
| campaign_source | Free-text marketing touch recorded at order entry. | Known data-quality gap: populated on a small minority of orders. Do not use for marketing ROI analysis without Finance review. |

## 3. Population counts (as of 2026-05-31)

The CRM holds 3,109 account records: 2,614 active, 495 dormant.

Active accounts by segment:

| Segment | Active accounts |
|---|---|
| STR — street/independent | 1,588 |
| CHN — regional chains | 96 |
| EDU — education (K-12 & college) | 411 |
| HCF — healthcare | 187 |
| EQP — equipment-only | 332 |
| **Total active** | **2,614** |

Portal enrollment: portal_enrolled = Y on 492 of 1,588 active street accounts (31.0%) as of 2026-05-31. Chain accounts transact via EDI and are excluded from portal metrics; education and healthcare accounts largely order against bid or contract catalogs and are tracked separately.

Lapse-flag incidence: 19.3% of active accounts triggered lapse_90d_flag at least once during FY2025. Reminder — the flag exists to feed the re-engagement queue. Seasonal education accounts trip it every summer by design, and street accounts that skip a quarter trip it and then resume. For any attrition or churn analysis, use the Controller's reconciliation memo (M. Ellery, 2026-02-12), which is the authoritative planning treatment.

## 4. Usage notes

1. Counts quoted externally (marketing copy, food-show materials, press) should use **active** accounts (2,614), not total records (3,109). Total records include 495 dormant accounts with no order in the trailing 365 days.
2. Per-account revenue math should divide segment revenue by the segment's **active** count, not the record count.
3. A stratified sample extract (crm_account_extract.csv, n=78) is published alongside this dictionary for reference and spot-checking. The sample is illustrative; the population counts in Section 3 govern.
4. lapse_90d_flag should never be labeled "churn" in reporting. If you need a churn figure, see the memo cited in Section 3.

Questions to Sales Ops. Definition changes require sign-off from the Controller's office.
