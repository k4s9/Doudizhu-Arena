# Reliability run 781a96ead0c7432bb68a90968d0387ac
Execution: real; integrity: True; status: finished
Manifest: b30ffb10d44bfa453b55c9b5fddb8d416653033da20c482abae33344e811f395
Tasks: {'finished': 3}; table-hands: {'finished': 6}
Known cost USD: 0; coverage: {'numerator': 290, 'denominator': 292, 'value': 0.9931506849315068}

| Variant | Phase | First availability | First output legality | Model success | Fallback |
|---|---|---|---|---|---|
| single_generation | bidding | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 0/5 (0.0%) |
| single_generation | playing | 99/100 (99.0%) | 99/99 (100.0%) | 99/100 (99.0%) | 1/100 (1.0%) |
| generic_retry | bidding | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 0/5 (0.0%) |
| generic_retry | playing | 90/91 (98.9%) | 89/90 (98.9%) | 91/91 (100.0%) | 0/91 (0.0%) |
| rule_feedback | bidding | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0/4 (0.0%) |
| rule_feedback | playing | 83/83 (100.0%) | 81/83 (97.6%) | 83/83 (100.0%) | 0/83 (0.0%) |

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

Failure traces: 5; see failure-cases/.
