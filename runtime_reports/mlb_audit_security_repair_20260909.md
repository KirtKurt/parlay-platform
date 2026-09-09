MLB audit and security repairs — 9 September 2026

The audit and monitoring defects are repaired and verified. Firewall and budget completion remain blocked by explicit AWS IAM denials. Public model predictions remain closed because no model has qualified.

Repairs merged in [PR #680](https://github.com/KirtKurt/parlay-platform/pull/680), merge commit `a276cb87668e996b671598724fa2b829676e1385`:

- All three AWS audit entrypoints now read the deployed trainer's cohort, release cutoff, feature contract, lease and table before starting a fresh audit interpreter. The publisher previously read retired R7 records.
- The auditor checks the deployed identity again before accepting its evidence, and uses the same ten-minute capture freshness limit as the live trainer.
- Fresh failed audit evidence is preserved while the failed check remains visible. Old checked-in reports cannot silently masquerade as a newly generated audit.
- REST API alarms now use the actual `ApiName` and `Stage` dimensions. Existing alarm notification settings are preserved.
- Stage throttling is configured independently of WAF permissions. Alarm and budget setup failures return failure. A denied WAF read cannot trigger an association attempt, and an existing different ACL is preserved for review.

Verification:

| Check | Result |
|---|---|
| Production source contract | Passed: 1,724 main tests plus 20 new binding/security tests; separate source and lock suites also passed |
| Fresh audit publisher | Passed and published to main at 14:06 UTC |
| Audit creation / age at freshness check | 14:03:12 UTC / 3.37 minutes, within 15-minute limit |
| Audit cohort | mlb-v2-2026-08-31-historical-live-r8 |
| Latest training / capture verified by audit | 13:22:35 UTC / 14:05:12 UTC; both healthy and matching deployed source 80af2e4 |
| Settled games / graded / missing predictions | 15 / 15 / 0 |
| API alarm read-back | All three REST API alarm definitions verified; observed state OK |
| API throttling read-back | 100 requests/second, burst 200 |
| Public model endpoint after control repair | HTTP 503, NO_QUALIFIED_CHAMPION, publication closed |
| WAF / budget verification | Blocked by AWS AccessDeniedException |

[Production contract](https://github.com/KirtKurt/parlay-platform/actions/runs/34360494821).
[Successful fresh audit](https://github.com/KirtKurt/parlay-platform/actions/runs/34360995373).
[Security application and verification](https://github.com/KirtKurt/parlay-platform/actions/runs/34360995526).
The fresh audit artifact is `10107967815`, SHA-256 `0f4c1b5e5e86af4f0be8d1dd60a17432fdda6f2c3881346858f4d2bca3a37d4b`. Its full audit and freshness JSON files were published by the workflow, replacing the August 30 evidence.
The security artifact is `10107845265`, SHA-256 `88acaf7342c1c90c7fd5dd6b0c71244f3ebab2c74fec3ebb8ca4d3a1d67b515b`. The companion security JSON contains the extracted read-back evidence and source link.

AWS administrator action still required:

The current principal is `arn:aws:iam::735707987003:user/github-parlay-platform-deploy`. AWS explicitly denied these operations because no identity-based policy allowed them:

| Operation | Denied IAM action | Resource from AWS denial |
|---|---|---|
| List regional WebACLs | wafv2:ListWebACLs | arn:aws:wafv2:us-east-1:735707987003:regional/webacl/*/* |
| Verify stage WebACL | wafv2:GetWebACLForResource | arn:aws:wafv2:us-east-1:735707987003:regional/webacl/*/* |
| Create named monthly budget | budgets:ModifyBudget | arn:aws:budgets::735707987003:budget/inqsi-monthly-cost-guardrail |
| Verify named budget | budgets:ViewBudget | arn:aws:budgets::735707987003:budget/inqsi-monthly-cost-guardrail |

An authorized AWS administrator must resolve these permissions or perform the setup using an authorized identity. No IAM policies or privileges were changed by this repair. The existing WAF setup may additionally need CreateWebACL and AssociateWebACL if the intended ACL is absent; those operations were not reached because listing was denied. After permissions are resolved, rerun **Repair and verify MLB security controls**. Its independent read-back must pass before claiming these controls are operational. The monthly budget is a cost monitor, not an enforced spending cap.

Model/data limitations:

The fresh 13:22 training run still has 115 usable successor inputs, split 11 training / 53 calibration / 51 selection, with zero settled starter-rate observations. It rejects 495 historical adapter records that lack the original immutable fundamentals snapshots. That is an intentional admission rule, not evidence of a newly broken importer. These records cannot be converted into authentic pregame observations by changing flags or timestamps. New starter observations must settle and pass the existing source checks.

Yesterday's locked prediction audit remains 6 correct out of 15 (40%). Repairs do not change historical predictions or outcomes. R8's sealed failed test is unchanged. The successor still needs sufficient development data, a frozen candidate, a successful fresh prospective test, and review of its exact qualifying evidence before public activation.

AWS references supporting the monitoring repair: [REST API metric dimensions](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-metrics-and-dimensions.html) and [AWS Cost Management permissions](https://docs.aws.amazon.com/cost-management/latest/userguide/billing-permissions-ref.html).
