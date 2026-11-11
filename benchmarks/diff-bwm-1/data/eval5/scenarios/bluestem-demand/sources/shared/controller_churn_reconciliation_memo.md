# MEMORANDUM

**To:** Priya Raghunathan, CFO
**Cc:** Dale Hutmacher, VP Sales
**From:** Marcus Ellery, Controller
**Date:** 2026-02-12
**Re:** Reconciling Customer Attrition Metrics — 19.3% vs 11.2% vs 8.5%

---

Priya,

Per your request following the January leadership meeting, this memo reconciles the customer "churn" figures currently in circulation. Three different numbers are being quoted for the same phenomenon. They are all computable from our systems; they do not measure the same thing. This memo states what each measures, walks the reconciliation, and identifies the figure finance uses for planning.

## 1. The three figures

| Metric | FY2025 value | What it actually measures |
|--------|-------------|---------------------------|
| CRM lapse_90d flag rate | 19.3% | Share of active accounts that recorded ANY 90-consecutive-day gap in ordering during FY2025 |
| True annual account attrition | 11.2% | Share of active accounts that stopped ordering and did not resume within 12 months |
| Net revenue attrition | 8.5% | FY2024 revenue of truly churned accounts as a share of total revenue |

## 2. Reconciliation ladder

**Step 1 — the 19.3% is a flag rate, not an attrition rate.** The CRM field lapse_90d is set whenever an account records no order for 90 consecutive days. Per the CRM data dictionary, it exists as a re-engagement trigger for the sales team. It resets nothing and forgives nothing: an account that skips one quarter and comes back is flagged identically to an account that leaves forever. In FY2025, 19.3% of active accounts tripped the flag at least once.

**Step 2 — remove seasonal education accounts (−5.4 points).** K-12 and college dining accounts stop ordering every June through August by design. The flag catches nearly the entire education book every summer. These accounts resume ordering when the contract year restarts in August; they have not churned in any commercial sense. Excluding accounts whose only lapse window falls in the June–August seasonal pattern removes 5.4 percentage points.

**Step 3 — remove accounts that resumed ordering within 12 months (−2.7 points).** Street accounts skip quarters: ownership transitions, renovations, seasonal patios, a rep dispute that gets patched. Accounts that lapsed 90+ days but placed orders again within 12 months are retained customers, not churn. This removes a further 2.7 points.

**Result: true annual account attrition = 19.3% − 5.4 − 2.7 = 11.2%.** These are accounts that stopped ordering in FY2025 and stayed stopped.

**Step 4 — weight by revenue.** Attrition is not uniform across account sizes. The accounts we actually lose skew below average: the FY2025 churned cohort averaged about $140K of trailing revenue — roughly three-quarters of the mean account's ~$185K (the churn concentrates in mid-size street accounts, not the very smallest). Weighting the churned cohort by its trailing FY2024 revenue, the revenue we lost to true churn was 8.5% of total company revenue. **Net revenue attrition = 8.5%.** This is the planning figure.

## 3. Summary table

| Step | Accounts basis | Rate |
|------|----------------|------|
| lapse_90d flag rate, FY2025 | any 90-day gap | 19.3% |
| less: seasonal education lapse pattern | | (5.4) pts |
| less: resumed ordering within 12 months | | (2.7) pts |
| **True annual account attrition** | no resumption in 12 months | **11.2%** |
| Revenue weighting (churned cohort ≈ $140K average, ~76% of the mean account) | trailing-revenue basis | **8.5%** |

## 4. Which figure to use, and where

Finance uses **8.5% net revenue attrition** for planning, budgeting, and any revenue-bridge work. Account-count analyses (e.g., rep coverage models) may use 11.2% where a count basis is genuinely needed.

The 19% figure should not appear in planning documents; it is a lapse flag, not an attrition rate. I am aware it has appeared in at least one leadership memo and one board pre-read this year. The field is doing its job — it tells a rep to call a quiet account — but reading it as "one in five customers leaves every year" overstates true attrition by roughly a factor of two on an account basis and by more than that on a revenue basis.

## 5. Housekeeping

The definitions above are consistent with the CRM data dictionary maintained by Sales Ops. If anyone requires a different attrition cut (by segment, by DC, by rep tenure), my team can produce it from the same order-history table within a day or two. I would only ask that any figure quoted outside finance carry its definition with it, which is, as ever, most of the battle.

M. Ellery
Controller
