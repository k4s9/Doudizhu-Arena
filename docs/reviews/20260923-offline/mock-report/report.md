# Reliability run 6bb8ab7304684d918568901ef696845d
Execution: mock; integrity: True; status: finished
Manifest: 8fd0ebecda8413859ec2ada302806ca7619660104ec19728770a8b7e2e4f78b8
Tasks: {'finished': 6}; table-hands: {'finished': 12}
Known cost USD: 0; coverage: {'numerator': 732, 'denominator': 732, 'value': 1.0}

| Variant | Phase | First availability | First output legality | Model success | Fallback |
|---|---|---|---|---|---|
| single_generation | bidding | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0/4 (0.0%) |
| single_generation | playing | 232/232 (100.0%) | 220/232 (94.8%) | 220/232 (94.8%) | 12/232 (5.2%) |
| generic_retry | bidding | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0/4 (0.0%) |
| generic_retry | playing | 232/232 (100.0%) | 220/232 (94.8%) | 232/232 (100.0%) | 0/232 (0.0%) |
| rule_feedback | bidding | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 0/4 (0.0%) |
| rule_feedback | playing | 232/232 (100.0%) | 220/232 (94.8%) | 232/232 (100.0%) | 0/232 (0.0%) |

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

Failure traces: 36; see failure-cases/.
