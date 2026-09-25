# Reliability run 33eac844f2e244c8a1b8898164b358e7
Execution: real; integrity: True; status: finished
Manifest: 4beeb007c56996d37ecc866121c9310e266aa544aeef8045b8189b227e196e17
Tasks: {'finished': 3}; table-hands: {'finished': 4, 'void': 2}
Known cost USD: 0; coverage: {'numerator': 275, 'denominator': 275, 'value': 1.0}

| Variant | Phase | First availability | First output legality | Model success | Fallback |
|---|---|---|---|---|---|
| single_generation | bidding | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 0/6 (0.0%) |
| single_generation | playing | 40/40 (100.0%) | 32/40 (80.0%) | 32/40 (80.0%) | 8/55 (14.5%) |
| generic_retry | bidding | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 0/6 (0.0%) |
| generic_retry | playing | 120/120 (100.0%) | 100/120 (83.3%) | 117/120 (97.5%) | 3/120 (2.5%) |
| rule_feedback | bidding | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 0/6 (0.0%) |
| rule_feedback | playing | 57/57 (100.0%) | 48/57 (84.2%) | 57/57 (100.0%) | 0/57 (0.0%) |

## Definitions
D: all started bidding/playing decisions including autoplay; L: decisions with an issued model call. R0: first provider returned (including empty text); V0: first returned output passed parser and rules. Availability R0/L; output legality V0/R0; first success V0/L. Model success counts model_first/model_retry only. Illegal recovery divides by all initially illegal returned outputs, even with no retry allowance. Fallback/autoplay divide by D. Retry divides by L. Normal table completion excludes void from the numerator but includes every started table in the denominator. Task attempts, cancellations, failures and unstarted plans are retained. Provider and end-to-end latency use nearest-rank, including failed calls. Unknown usage/cost is NULL, never zero.

## Limits
mock results measure plumbing only
self-play does not establish competitive advantage
seed is the independent cluster; decision counts are correlated
P95 uses nearest-rank; small samples are descriptive
unknown usage retains reservation; known totals are lower bounds when coverage is incomplete

## Integrity issues
None

Failure traces: 37; see failure-cases/.
