# 固定局面规则反馈效果实验：minimax-m27

完整性审计：通过。
实际调用 292 次；usage 已知 292/292。

三协议共享首答；仅失败时分别执行最多两次通用重试和规则反馈重试。

| 阶段 | 协议 | 成功/样本 | 首答非法后恢复 | 逻辑调用 | 平均调用耗时合计 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| all | single_generation | 154/192 | 0/38 | 192 | 10980.1 |
| all | generic_retry | 184/192 | 30/38 | 243 | 13752.1 |
| all | rule_feedback | 189/192 | 35/38 | 241 | 13772.8 |
| bidding | single_generation | 23/24 | 0/1 | 24 | 9893.9 |
| bidding | generic_retry | 24/24 | 1/1 | 25 | 10333.1 |
| bidding | rule_feedback | 24/24 | 1/1 | 25 | 10287.4 |
| playing | single_generation | 131/168 | 0/37 | 168 | 11135.2 |
| playing | generic_retry | 160/168 | 29/37 | 218 | 14240.6 |
| playing | rule_feedback | 165/168 | 34/37 | 216 | 14270.7 |

逻辑调用包含各协议引用的共同首答，不能相加当成实际消费。零分母表示 N/A。

主指标（出牌）：规则反馈减通用重试为 **2.976 个百分点**，
按 24 个独立来源种子配对 bootstrap 的 95% 区间为 **[-1.190, 6.548]**。
区间跨过零时，本次样本不足以确认稳定的增益或劣化。

## 解释边界

- Frozen deterministic rule-policy states with fixed category quotas; not representative human play or competitive strength.
- Shared first responses correlate protocols deliberately; do not count three independent baseline samples or sum logical calls as actual expenditure.
- One response realization per state/model; inference clusters by source seed. It does not cover all provider-time/version variability.
- Call-time estimates exclude queue/branch scheduling; they are not measured live-game end-to-end latency.
- Requested aliases may resolve to different returned names; names/fingerprints cannot prove weight-version identity.
- Zero prices follow the previously user-confirmed free account, not an audited provider bill; unknown usage remains unknown.
- A degenerate zero bootstrap interval does not establish equivalence or absence of rare failures.
- The validator and source replay reuse the project engine; this is not an independent third-party rules audit.
