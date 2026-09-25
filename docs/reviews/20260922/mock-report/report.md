# Reliability run ac0ccc037ba6471c8e83d3f12c78fd7c

Execution: mock; integrity: True; status: finished

Manifest: 35756aa2858e1b3273e750e25bbd59c2561d1d8f715f1f11d5460e25d9e2ce21

Tasks: {'finished': 6}; table-hands: {'void': 4, 'finished': 8}

Known cost USD: 0; coverage: {'numerator': 420, 'denominator': 420, 'value': 1.0}



| Variant | Phase | First availability | First output legality | Model success | Fallback |

|---|---|---|---|---|---|

| single_generation | bidding | 12/12 (100.0%) | 0/12 (0.0%) | 0/12 (0.0%) | 12/12 (100.0%) |

| single_generation | playing | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |

| generic_retry | bidding | 4/4 (100.0%) | 0/4 (0.0%) | 4/4 (100.0%) | 0/4 (0.0%) |

| generic_retry | playing | 164/164 (100.0%) | 144/164 (87.8%) | 152/164 (92.7%) | 12/232 (5.2%) |

| rule_feedback | bidding | 4/4 (100.0%) | 0/4 (0.0%) | 4/4 (100.0%) | 0/4 (0.0%) |

| rule_feedback | playing | 164/164 (100.0%) | 144/164 (87.8%) | 152/164 (92.7%) | 12/232 (5.2%) |



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



Failure traces: 60; see failure-cases/.