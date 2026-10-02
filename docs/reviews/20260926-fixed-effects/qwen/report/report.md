# 固定局面规则反馈效果实验：qwen3.5

完整性审计：未通过；不能据此作完整效果结论。
实际调用 198 次；usage 已知 197/198。

三协议共享首答；仅失败时分别执行最多两次通用重试和规则反馈重试。

| 阶段 | 协议 | 成功/样本 | 首答非法后恢复 | 逻辑调用 | 平均调用耗时合计 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| all | single_generation | 189/192 | 0/2 | 192 | 26394.8 |
| all | generic_retry | 192/192 | 2/2 | 195 | 26675.0 |
| all | rule_feedback | 192/192 | 2/2 | 195 | 26592.7 |
| bidding | single_generation | 24/24 | 0/0 | 24 | 22806.0 |
| bidding | generic_retry | 24/24 | 0/0 | 24 | 22806.0 |
| bidding | rule_feedback | 24/24 | 0/0 | 24 | 22806.0 |
| playing | single_generation | 165/168 | 0/2 | 168 | 26907.5 |
| playing | generic_retry | 168/168 | 2/2 | 171 | 27227.7 |
| playing | rule_feedback | 168/168 | 2/2 | 171 | 27133.7 |

逻辑调用包含各协议引用的共同首答，不能相加当成实际消费。零分母表示 N/A。

## 解释边界

- Frozen deterministic rule-policy states with fixed category quotas; not representative human play or competitive strength.
- Shared first responses correlate protocols deliberately; do not count three independent baseline samples or sum logical calls as actual expenditure.
- One response realization per state/model; inference clusters by source seed. It does not cover all provider-time/version variability.
- Call-time estimates exclude queue/branch scheduling; they are not measured live-game end-to-end latency.
- Requested aliases may resolve to different returned names; names/fingerprints cannot prove weight-version identity.
- Zero prices follow the previously user-confirmed free account, not an audited provider bill; unknown usage remains unknown.
- A degenerate zero bootstrap interval does not establish equivalence or absence of rare failures.
- The validator and source replay reuse the project engine; this is not an independent third-party rules audit.
