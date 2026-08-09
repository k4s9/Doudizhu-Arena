# 斗地主 LLM Agent 可靠性与公平评测闭环计划

## 目标与范围

将项目从“可运行的斗地主 LLM 对战网站”补强为一个可复现、可比较、可审计的 LLM Agent 决策可靠性评测系统。

本计划严格限定在评测闭环：不扩展斗地主业务玩法、不改变现有复制赛（AB 双桌同牌）规则。评测的配置、运行控制、进度、指标和失败案例需要同步提供 Web 管理界面，使系统既能用于研究，也能作为真实网站持续运营。

## Web 管理与凭据安全补充

- Web 提供供应商/模型配置管理；API token 由服务端使用 `DOUDIZHU_CREDENTIAL_MASTER_KEY` 加密后写入 SQLite，主密钥只从服务端环境变量读取，接口永不回传明文 token。
- 模型配置作为可复用模板，至少包含 provider、model、base URL、采样参数与系统提示词；牌手从模板与预制提示词派生，避免每个牌手重复填写供应商参数。
- Web 提供实验列表、创建与预检、运行详情、显式真实费用确认、取消和恢复。阶段 5 增加指标仪表盘与同 seed 方案对比，阶段 6 增加失败案例时间线及回放入口。
- 首版消融实验固定单一模型，以隔离策略变量；后续跨供应商对局通过多个模型模板组装阵容，但不得混入同一消融结论。

## 现状审计

### 已具备的基础

| 能力 | 当前实现 | 依据 |
| --- | --- | --- |
| 固定发牌 | `MatchConfig.seed` 支持指定比赛种子；每手派生为 `seed/hand-{n}`，`Deck(hand_seed)` 生成牌局 | `src/backend/arena/tournament/match.py` |
| 公平同牌对照 | A/B 两桌复用同一 `DealResult`，且已有差分计分 | `src/backend/arena/tournament/match.py`、`arena/tournament/diff_scoring.py` |
| 模型标识 | 玩家配置持久化 `provider`、`model`、`base_url`，调用日志也保存 provider/model | `src/backend/arena/db/models.py`、`arena/llm/logging.py` |
| Token/单调用延迟原始字段 | `LLMUsage` 与 `llm_call_logs` 设计了 prompt/completion/total tokens、`latency_ms` | `src/backend/arena/llm/base.py`、`arena/db/models.py` |
| 输出校验和重试 | `LLMAgent` 对叫分、出牌、复盘、总结均进行解析校验，并最多重试 3 次；重试提示包含错误原因 | `src/backend/arena/agent/llm_agent.py`、`arena/agent/parser.py` |
| 托管/降级动作 | 超时或连续失败进入托管；出牌动作有 `is_trusted` 标记，reasoning 中也带“托管” | `src/backend/arena/engine/timeout.py`、`arena/tournament/table.py` |
| 记忆机制 | 每手反思更新短期记忆、每场总结更新长期记忆，并持久化反思与记忆文本 | `src/backend/arena/tournament/table.py`、`arena/tournament/match.py`、`arena/agent/memory.py` |
| 可回放原始证据 | 保存叫分、出牌、思考、初始/剩余手牌及回放 API | `src/backend/arena/db/models.py`、`arena/api/routes/replay.py` |

### 关键缺口

1. **调用日志在正式比赛路径未接入。** `LoggingLLMProvider` 已实现，但 `POST /matches/{id}/start` 直接构造 `ClaudeProvider`/`OpenAIProvider`；因此 `llm_call_logs` 不会可靠地产生真实比赛数据。
2. **模型版本不可审计。** 仅保存模型别名，未记录 provider 返回的实际版本、API/base URL 版本、采样参数、提示词版本、代码提交和依赖锁定信息。
3. **无实验实体及运行器。** 当前只有单场比赛 API，没有 `experiment/run/variant` 数据模型、固定种子清单、批量调度、续跑与失败恢复。
4. **可靠性指标没有事件级事实来源。** `agent_thoughts.retry_count`、`llm_call_ms` 有字段但当前写入时未赋值；非法输出在内部被重试或回退，未以结构化事件持久化；超时、异常、托管也无法按“请求/决策/对局”统一聚合。
5. **没有指标聚合和报告。** 现有数据不能直接产出完成率、非法输出率、重试率、托管率、平均/P95 延迟、Token、调用成本及置信区间。
6. **无消融控制。** “无规则反馈 / 带规则反馈 / 带复盘记忆”没有明确配置边界；当前重试反馈固定启用，反思与赛后总结默认执行。
7. **真实模型评测尚未执行。** 现有测试均使用 mock provider，`test_provider_comparison.py` 也只是 mock 的提示词/行为对比，不构成真实模型比赛。
8. **无失败案例档案。** 回放可提供素材，但尚无固定筛选、脱敏、根因、修复、回归验证和前后指标对比流程。

## 评测设计

### 固定实验协议

- **实验单位：** 一次 experiment 由若干个 variant、固定模型清单、固定配置快照和一个不可变的种子集组成；每个 variant 对每个 seed 运行相同局数与相同座位轮换。
- **种子：** 提交 `evaluation/seed_sets/v1.json`，包含至少 100 个显式 match seed；按 `seed_set_id` 和 `seed_set_sha256` 标识。运行时禁止以当前时间补种子。
- **公平性：** 每一 variant 使用完全相同的 seed 集、`total_hands`、KO 策略、超时、座位轮换、对手与评分规则；AB 双桌继续共享每手 `DealResult`。建议评测模式关闭 KO，以保证每个 seed 的观测局数一致。
- **模型快照：** 对每个参赛配置记录 `provider`、请求 `model`、`base_url`、可得的响应模型版本/系统指纹、温度/随机种子/最大输出 token、SDK 版本、`git rev-parse HEAD`、`pyproject.toml`/锁文件哈希、系统提示词与规则提示词哈希。若供应商不返回版本或指纹，报告必须明确标为“不可验证的别名”。
- **真实调用约束：** 仅通过显式 `--real-models` 或环境开关启动，启动前检测 API key、模型清单和单实验预算上限；默认 dry-run/mock，避免测试意外消耗费用。
- **对局隔离：** 每个 seed x variant 使用独立的 player 实例和新 match；除“带复盘记忆”外不读取或写回长期记忆。

### 待比较方案

先比较同一模型、同一系统提示词、同一赛事条件下的下列三种方案；不得将模型、提示词风格或超时参数混入变量。

| variant_id | 重试时规则反馈 | 手后复盘/短期记忆 | 赛后长期记忆 | 用途 |
| --- | --- | --- | --- |
| `baseline_no_feedback` | 关闭；非法响应直接计入并执行既有安全回退 | 关闭 | 不读、不写 | 量化裸模型约束遵从能力 |
| `rule_feedback` | 启用当前解析错误及规则约束反馈 | 关闭 | 不读、不写 | 隔离规则反馈对格式/合法性的作用 |
| `reflection_memory` | 启用 | 启用 | 在预先定义的训练 seed 上写入，在独立冻结的评测 seed 上只读 | 衡量复盘记忆是否提升决策稳定性 |

“带复盘记忆”必须采用训练/评测种子严格分离：例如 `train-v1` 只生成记忆，`eval-v1` 只读取该记忆且禁止评测中写回，避免同一牌局反馈泄漏到结果。

### 指标口径

每条指标需同时提供：总体、按 variant、按 provider/model、按 phase（bidding/playing/reflection/summary）和按 seed 的值；报告中区分“仅决策调用”和“含复盘/总结的全链路调用”。

| 指标 | 分子 / 分母 | 说明 |
| --- | --- | --- |
| 对局完成率 | 正常完成 match 数 / 已启动 match 数 | 正常完成指两桌完成配置规定的手数，且无未处理异常或意外中止；void hand 另列，不等同失败。 |
| 非法输出率 | 被解析器拒绝的 LLM 原始响应次数 / LLM 原始响应次数 | 细分 JSON 格式、字段、手牌不存在、牌型非法、无法压过、叫分非法、空响应、provider 错误；每次 retry 前的失败都计入。 |
| 重试率 | 发生至少一次重试的决策数 / LLM 决策数 | 同时报 `retry_attempts / LLM 决策数` 与平均重试次数；反思/总结可另列。 |
| 托管率 | 托管决策数 / 应由 agent 决策数 | 细分主动托管、超时进入托管、连续失败降级；叫分自动 pass 也必须纳入。 |
| 平均/P95 延迟 | 单次 provider 调用端到端耗时 | P95 使用 nearest-rank，样本数 `n` 一并报告；另报决策端到端延迟（含重试）。 |
| Token | 所有可得 usage 的 input/output/total 求和与每完成对局均值 | 不可获得 usage 时保存 `NULL` 并报告覆盖率，绝不当作 0。 |
| 调用成本 | `input_tokens * input_price + output_tokens * output_price` | 费率来自版本化 `evaluation/pricing/v1.yaml`；按 provider/model/生效日期记录，未知费率为 `NULL`，报告覆盖率。 |
| 竞技结果（辅助） | 胜率、平均差分分、每 seed 差分分 | 是策略质量信号，不能替代可靠性指标。对同 seed 的 variant 使用配对比较。 |

报告为比例指标给出 Wilson 95% CI；延迟和成本同时给出样本数、均值、中位数、P95、最大值。为了防止终局被回退掩盖，所有“最终合法动作”之外的原始非法响应必须保留事件。

## 实施计划

## 进度快照（2026-08-09）

> 本节是当前代码状态的权威进度记录；上方“现状审计”保留立项背景，其中“正式比赛未接入调用日志”和“`agents.yaml` 含明文 key”已经修复。2026-08-09 审计确认：评测专项测试 14 项通过，dry-run 可稳定生成 300 个任务；当前数据库中 evaluation run、评测 match、decision event、LLM call 均为 0，因此不能把基础设施完成等同于真实评测闭环完成。

| 阶段 | 状态 | 已完成 | 仍需完成 |
| --- | --- | --- | --- |
| 阶段 1：规格与安全边界 | 进行中 | 100 个冻结 eval seeds、20 个独立 training seeds、三 variant spec、确定性 manifest、显式 real-model 开关；manifest 已记录 dirty worktree、依赖哈希、SDK 版本和冻结记忆哈希；OpenAI-compatible 请求可传固定 seed | 提交价格表并校验预算；将实际模型、运行时系统提示词/模板和凭据配置做不可变快照；正式运行前替换示例模型占位符 |
| 阶段 2：决策与调用观测 | 进行中 | 四个 phase 统一使用 variant retry 与规则反馈；稳定 `decision_id`；最终非法尝试不再重复写事件；错误按主要解析/规则/provider 类别记录；调用日志可关联 run/variant/match/decision/attempt 并保存响应模型、system fingerprint、usage 和 latency | 完善安全摘要与错误枚举；明确 provider 错误是否进入“非法输出率”；实现独立的 runner-level transient retry 口径 |
| 阶段 3：数据库与查询 | 进行中 | 实验、任务、模型快照、决策事件和训练 checkpoint 表已实现；提供 v3→v4 向前迁移；专项迁移测试通过 | 指标聚合、同 seed 配对、失败候选和完整性审计查询；修复相同 manifest 只能复用同一 run、无法创建独立重复实验的问题 |
| 阶段 4：批量运行器 | 进行中 | 300 任务 dry-run、顺序执行状态机、取消/恢复基础、真实调用确认；训练 seeds 可 checkpoint 并导出冻结 memory artifact；只读评测校验 artifact 哈希 | 完整 mock match E2E、任务完成语义校验、runner-level transient retry、可选受控并发、真实 memory training/smoke/pilot；当前仓库尚无真实 memory artifact |
| 阶段 5：指标与报告 | 未开始 | — | 价格表、可靠性指标、延迟/Token/成本、置信区间和报告制品 |
| 阶段 6：失败案例 | 未开始 | 已有事件和回放基础 | 候选筛选、案例文档、回归修复及前后报告 |
| 阶段 7：模型模板与牌手派生 | 暂停扩展 | 环境变量凭据、SQLite 加密、Web 写入保护和牌手派生基础 | 本轮不再增加页面；仅补足真实评测必需的连通性预检与模板/提示词不可变快照 |

### 简历项目标准验收结论

**结论：尚未达到。** 当前已经具备较完整的评测基础设施和可测试的数据采集路径，但还没有任何真实评测运行或结果制品，不能形成可引用的实验结论。

| 简历标准 | 状态 | 2026-08-09 可验证证据 | 达标缺口 |
| --- | --- | --- | --- |
| 固定发牌种子和模型版本 | 部分完成 | `eval-v1` 固定 100 seeds，`train-v1` 固定 20 seeds；manifest 固定请求模型、采样参数、提示词代码哈希、SDK/依赖/Git 信息，调用日志可保存响应模型和 system fingerprint | 正式 spec 仍是 `replace-before-real-run`；运行时自定义系统提示词/模型模板未完整冻结；供应商不返回实际版本时需在报告标注不可验证别名 |
| 使用真实模型完成多轮比赛 | 未完成 | 运行器和显式真实调用开关已实现，dry-run 为 3 variants x 100 seeds = 300 tasks，每 task 20 hands | 数据库中 evaluation run 和 `eval:*` match 均为 0；尚无真实训练、smoke、pilot 或正式冻结评测 |
| 完成率、非法输出率、重试率、托管率 | 部分完成 | `decision_events`、任务状态、fallback/autoplay 事件和关联字段已实现 | 尚无聚合查询、分母完整性审计、置信区间、结果文件或真实数据 |
| 平均/P95 延迟、Token 与调用成本 | 部分完成 | 调用日志已具备 latency 和 usage 原始字段 | 尚无 nearest-rank P95、usage 覆盖率、版本化价格表、成本计算和预算硬限制 |
| 比较三种方案 | 部分完成 | `baseline_no_feedback`、`rule_feedback`、`reflection_memory` 已固化；训练/评测 seeds 分离，记忆只读并校验哈希 | 尚无真实冻结 memory artifact、同 seed 三方案完整运行、配对比较或统计不确定性 |
| 典型失败案例和修复过程 | 未完成 | 已有事件与回放基础 | 无候选筛选、案例文档、针对性修复、回归测试和修复前后同 seed 对比 |

### 2026-08-09 审计发现的阻塞风险

1. `evaluation_runs.manifest_sha256` 当前为唯一键，`insert_evaluation_run` 使用 `INSERT OR IGNORE`。相同不可变 manifest 会返回旧 run，而不是创建一次独立重复运行；这会阻碍重复性验证，也可能让“重新运行”意外复用旧任务。
2. `pricing_version` 和 `budget_limit_usd` 目前只进入 spec，没有价格表加载、费用预估或运行中硬停止，真实调用的成本安全边界尚未闭合。
3. runner 将 `_run_task` 无异常返回直接标记为 finished，正式统计前应增加 match 状态、规定手数、两桌结果与事件/调用关联的完整性审计，避免“执行返回”被误算为“对局正常完成”。
4. 当前真实实验 spec 使用示例配置名与模型占位符，且缺少冻结 memory artifact；按现状不能启动三 variant 正式评测。
5. 当前 live DB 尚未产生任何评测调用数据；专项测试验证的是结构和替身路径，不是 provider usage、response model、system fingerprint 在真实响应上的覆盖率。

### 本轮数据可信度修正

- [x] 叫分、出牌、复盘、总结统一使用 variant 的 `retry_limit` 与 `rule_feedback`。
- [x] 每个决策使用稳定 `decision_id`；调用日志可关联 run、variant、match、table hand 和 attempt。
- [x] 最终非法输出只保留一条原始尝试事件，并在同一事件标记 fallback，避免分母重复。
- [x] 记录 provider 实际 response model/system fingerprint（供应商提供时）。
- [x] manifest 记录 Git dirty 状态、依赖哈希和 SDK 版本。
- [x] `reflection_memory` 只读评测必须提供冻结 memory artifact 并校验哈希；评测阶段不再执行无写回价值的赛后 summary。
- [x] 新增 v3→v4 迁移与决策—调用关联回归；评测专项测试当前 14 项通过。
- [x] 实现训练 seeds → memory artifact 的 checkpoint、版本化和冻结导出流程；专项测试覆盖恢复数据与哈希校验。
- [x] 2026-08-09 评测专项测试更新为 14 项通过，100-seed dry-run 稳定生成 300 tasks。
- [ ] 修复重复 run 身份、任务完成语义和预算硬限制三个数据可信度阻塞项。
- [ ] 实现指标聚合、价格表和报告后再允许正式 100-seed 评测。
- [ ] 生成真实冻结 memory artifact，完成真实模型 smoke、20-seed pilot 和最终冻结评测。
- [ ] 从真实 run 归档一个失败案例，完成最小修复、回归测试及修复前后同 seed 报告。

### 下一步实现顺序（停止新增页面）

1. **先封闭数据正确性。** 修复重复 run 身份；为 task finished 增加 match 完整性条件；定义 provider error、非法输出、retry 和 autoplay 的互斥/包含口径；增加 run 完整性审计。
2. **实现最小可用报告链。** 新增版本化 `pricing-v1.yaml` 与 `arena.evaluation.report`，优先输出 `summary.json`、`metrics.csv`、`seed_level.csv`、`report.md`；精确测试完成率、非法率、重试率、托管率、nearest-rank P95、usage/cost 覆盖率、Wilson CI 和同 seed 配对。
3. **补完整 mock E2E。** 真实跑完小型的三 variant x 同 seed match，而不是 monkeypatch `_run_task`；断言 task→match→decision→call→report 全链可追溯，并覆盖中断恢复、部分失败和缺失 usage。
4. **冻结真实运行输入。** 确认单一 provider/model 与预算，将实际模板、系统提示词、采样参数、价格生效日期和 memory artifact 写入 manifest；预检失败时禁止真实调用。
5. **按 smoke → pilot → 正式评测推进。** 先 1-2 seeds 验证真实 usage/版本字段，再跑独立 20-seed pilot 校准预算和超时；修复工程问题后冻结代码与配置，最后运行 100-seed 三方案评测。
6. **完成失败案例闭环。** 从正式/回归 run 自动筛选一个可复现失败，保留脱敏事件链和回放；做最小修复并用同一冻结 seeds 复跑，形成 `failure-case-001.md` 和修复前后报告。

### 阶段 1：实验规格、可复现性与安全边界

1. 新建 `src/backend/arena/evaluation/` 包，定义 `ExperimentSpec`、`VariantSpec`、`ModelSnapshot`、`RunManifest` 和 JSON Schema/Pydantic 校验；实验文件放入 `evaluation/experiments/`，种子集放入 `evaluation/seed_sets/`。
2. 在 spec 中固定 `experiment_id`、seed-set、模型配置版本、座位轮换、局数、timeout、retry、价格表版本、代码提交和提示词哈希。对同名实验运行创建不可变 manifest，而非覆盖配置。
3. 为 provider 请求参数建立显式配置（至少 `max_tokens`、温度、top_p、供应商可用的随机 seed）；没有确定性 API 支持时，在 manifest 标注“模型采样不可完全复现”，但仍固定所有可控因素。
4. 添加预检：seed 唯一性、variant 组合完整性、评测与记忆训练种子不交集、模型配置/API key 可用、费用价格完整性、总预算/单 run 调用数上限。真实调用需要 `--real-models`，并将预计上限写入 run manifest。
5. 将 `agents.yaml` 中明文 key 移出版本控制，改为 `${ENV_VAR}`；保留占位示例。对已有泄露的密钥立即在供应商侧轮换。此项是运行真实评测前的阻塞项。

验收：相同 spec 在不调用模型的 dry-run 下生成完全相同的 run plan、seed 序列和配置哈希；任何未固定 seed、价格或模型快照的 real run 被拒绝。

### 阶段 2：决策与调用级可观测性

1. 在 `LLMAgent` 引入返回元数据的决策结果或回调，不破坏现有 `Agent` 协议；每次原始响应、解析失败、provider 异常、重试、最终回退都生成结构化 `DecisionEvent`。字段至少包括 experiment/run/variant/match/hand/table/seat/player、phase、attempt、原始响应 hash、解析错误分类、是否非法、是否重试、是否 fallback、最终动作、决策耗时。
2. 让 `MAX_RETRIES` 从 variant 注入，不再硬编码；将“无规则反馈”定义为 `retry_limit=0` 或重试时不附加纠错内容，并保留一次非法尝试事件。
3. 在正式比赛 agent 构造路径包裹 `LoggingLLMProvider`，并在每个 table hand 设置上下文；扩展日志记录 request/response usage、调用序号、attempt、run/variant/match 关联和 provider 响应版本/系统指纹（可得时）。
4. 让 `TableRunner` 把真正的 `retry_count`、决策端到端耗时、回退原因和托管原因写入持久化记录；叫分、出牌、超时、异常、验证回退使用同一枚举分类。现有 `agent_thoughts.llm_call_ms` 与 `retry_count` 不再空置。
5. 定义最小必要留存：DB 保存事件元数据、错误分类、输出 hash 和截断安全摘要；原始 prompt/response 仅在显式 debug 目录中加密/限权保存，报告中不暴露 API key 或完整隐藏信息。

验收：使用 deterministically invalid 的测试 provider，验证每次非法响应、每次重试、最终 fallback 和托管均有一条可关联事件；使用真实 provider 的一场短赛能够在 `llm_call_logs` 看到非空 token/latency（若供应商返回 usage）。

### 阶段 3：数据库迁移与查询层

1. 采用向前兼容迁移，新增 `experiments`、`experiment_variants`、`evaluation_runs`、`decision_events`、`model_snapshots` 和可选 `run_artifacts`；以现有 `matches` 为 run 的子记录，不删除或重写用户已有比赛数据。
2. 为 `decision_events` 建立 `(run_id, variant_id, phase)`、`match_id`、`error_code`、`is_fallback`、`is_autoplay` 索引；将 `llm_call_logs` 与 event 通过 `decision_event_id`/调用序号关联。
3. 添加 repository 查询：run 完成状态、每 variant/phase 的分子分母、调用明细、按 seed 的配对结果、失败候选清单。SQLite P95 在应用层以完整排序或流式选择计算，禁止误用平均替代 P95。
4. 写 migration 与幂等初始化测试；验证旧数据库可启动、旧回放可访问、迁移不会覆盖现有配置、玩家和比赛。

验收：一份临时旧 schema DB 升级后通过原有 M4 集成测试；新 run 的所有比赛、事件、调用、模型快照可经外键/ID 追溯。

### 阶段 4：批量实验运行器

1. 新建 CLI，例如：

```bash
conda run -n doudizhu-arena python -m arena.evaluation.run \
  --spec evaluation/experiments/reliability-v1.yaml --dry-run

conda run -n doudizhu-arena python -m arena.evaluation.run \
  --spec evaluation/experiments/reliability-v1.yaml --real-models \
  --max-concurrency 2 --resume
```

2. 运行器按 `variant x seed x seat_rotation` 生成任务；每个任务创建独立 match、固定 seed 和独立 agent/memory scope。并发采用受控 semaphore，默认小并发，避免供应商限流改变超时和成本。
3. 对 transient provider 失败配置有限、可观测的 runner-level 重试；不要把它混入 agent 规则重试。任务状态分为 planned/running/finished/failed/cancelled，支持 `--resume`，并持久化失败原因。
4. 测试模式以 mock provider 覆盖顺序、幂等、恢复、超时、部分失败和跨 variant seed 等价性；真实模型 smoke 只运行 1-2 固定 seed，须由操作者明确启动，结果打 `smoke` 标签，不能与正式结果混合。
5. 首次正式实验建议：3 variants x 100 eval seeds x 20 hands，关闭 KO、固定座位轮换；若预算受限，先以 20 seeds 做预注册 pilot，只用 pilot 校正预算与工程问题，不作为最终比较结论。
6. 同步实现 Web 实验控制台：实验列表、配置表单、预检结果、任务数量与预算提示、真实调用二次确认、状态轮询、任务明细、取消及恢复。Web 与 CLI 必须调用同一运行器和状态机。

验收：dry-run 列出确定数量的任务及每个任务的 seed/variant/model 快照；中断后 `--resume` 不重复已成功任务；mock run 产生完整、可聚合的事件和对局。

### 阶段 5：指标计算、比较报告与结果制品

1. 实现 `arena.evaluation.report`：从单个 `run_id` 计算指标口径中的所有分子、分母、覆盖率与统计量，输出 `summary.json`、`metrics.csv`、`seed_level.csv` 与 `report.md` 到 `evaluation/results/<run_id>/`。
2. 同时生成三类视图：可靠性总表、延迟/Token/成本分 phase 表、同 seed 配对的 variant 差值表。报告在每个数字旁列 `n`、缺失率和对应数据范围。
3. 方案比较采用配对单位（同 seed、同座位轮换）的 bootstrap 95% CI；完成率/非法输出率/托管率还给出 Wilson CI。若样本不成对、seed 缺失或模型快照不同，则自动降级为描述性比较并明确标注不可归因。
4. 版本化价格表；结果中永远输出价格表版本、币种、计价单位、Token usage 覆盖率以及“已计价成本/总调用”覆盖率。模型供应商不回传 usage 时，不估算为零。
5. 增加只读 CLI 与 Make 目标，并将报告生成加入 CI 的 mock 回归；实际成本数字不进入单元测试断言。
6. 增加 Web 指标页：完成率、非法输出率、重试率、托管率、平均/P95 延迟、Token 与成本总览；支持按 variant、provider/model、phase 和 seed 筛选，并显示样本数、覆盖率与置信区间。

验收：利用合成 fixture 精确断言平均值、nearest-rank P95、比例分母、成本、NULL 覆盖率与配对差；真实 run 能生成不含密钥的独立 Markdown 报告。

### 阶段 6：失败案例闭环与项目叙事

1. 建立失败筛选规则：优先选取“非法输出耗尽重试后回退”“超时触发托管”“相同 seed 下 baseline 失败而规则反馈修复”“复盘记忆造成退化”的单一、可复现案例。
2. 为每次正式 run 自动输出候选清单，字段包含 seed、variant、模型快照、完整事件链、回放链接/ID、原始输出安全摘要、影响（回退/分差/成本/时延）。
3. 撰写 `docs/evaluation/failure-case-001.md`：实验条件、期望规则、实际事件时间线、根因、最小修复、回归测试、修复前后同一冻结 seed 集的指标变化、残余风险。原始输出按密钥与个人信息脱敏。
4. 修复应首先落在解析/提示/反馈/超时边界之一，且新增针对该失败模式的 unit/integration test；复跑冻结 eval seed，确认修复没有通过更改 seed、对手、时间限制或统计口径来制造改善。
5. 更新 README：说明项目研究问题、评测协议、可复现命令、结果目录、指标定义与已知限制；不将单个模型或单次 run 的结果泛化为普遍结论。
6. 在 Web 中提供失败候选列表与案例详情，展示脱敏事件时间线、错误分类、重试/回退链、关联回放、修复版本及同 seed 修复前后对比。

### 阶段 7：Web 模型模板与牌手派生

1. 将现有配置管理升级为供应商与模型模板管理，支持 OpenAI-compatible、Anthropic 等供应商的 base URL、token、模型名、采样参数及连通性测试。
2. token 新增/更新只接受写入，不返回明文；列表仅显示是否已配置、加密状态和最后更新时间。主密钥缺失时拒绝从 Web 写入 token，并给出部署提示。
3. 提供“从模板创建牌手”流程：选择模型模板、选择预制提示词或自定义提示词、设置展示名与初始记忆；允许从不同供应商模板派生牌手并组建红蓝阵容。
4. 对模板和提示词建立不可变快照；实验启动后修改模板不影响已创建 run 的模型/提示词哈希。

验收：可以完全从 Web 配置两个供应商的 token 和模型模板，派生不同牌手，创建跨供应商普通比赛；token 在 SQLite 中为带版本前缀的密文，任何列表与详情 API 均不含明文。

验收：至少一份失败案例从 run manifest 到回放、事件、代码修复、测试与前后报告可完整追溯。

## 测试矩阵

| 层级 | 重点 |
| --- | --- |
| 单元测试 | variant 注入、非法分类、retry/fallback/托管事件、成本计算、P95、置信区间、seed 派生 |
| 持久化测试 | 迁移幂等、旧 DB 兼容、run/event/call 关联、缺失 usage 的 NULL 语义 |
| 集成测试 | 固定 seed 的 mock 多 variant 批跑、resume、超时/网络错误、内存隔离、报告产物 |
| 回归测试 | 已归档失败案例的输入及预期修复行为 |
| 真实模型 smoke | 经显式许可的一至两个固定 seed，验证 provider、日志、usage、报告，不用于统计结论 |

测试与脚本均使用 `/home/k4s9/miniconda3/envs/doudizhu-arena/bin/python` 或 `conda run -n doudizhu-arena python`，符合项目 Python 环境约束。

## 交付顺序与完成定义

1. 先完成阶段 1-3，确保测量对象、数据和可重现性正确；未完成这些前不得跑正式真实模型比较。
2. 完成阶段 4 的 mock end-to-end 与阶段 5 的合成数据校验后，执行经预算确认的真实模型 pilot。
3. Pilot 通过后运行预注册的冻结 seed 集，生成正式报告并完成阶段 6 的失败案例修复。

项目达到本计划目标的最低完成条件：

- 一份版本化实验规格和不可变 run manifest 固定 seed、模型快照、提示词/代码/价格版本；
- 至少一次真实模型、多轮、三 variant 的冻结评测完成并有可重跑命令；
- 自动报告完整列出完成率、非法输出率、重试率、托管率、平均/P95 延迟、Token 与成本，并说明覆盖率和统计不确定性；
- 结果可按相同 seed 配对比较三种方案；
- 至少一个典型失败案例具备回放、根因、修复、自动化回归测试和修复前后对比；
- 所有真实成本和密钥均不进入代码库、测试日志或公开报告。

## 实施前需由项目负责人确认的决策

1. **参评模型与预算：** 确认可使用的 provider/model、每个模型的固定版本策略、总预算和单 run 硬上限。
2. **研究对象：** 首轮是否仅评测当前 Qwen/Minimax 配置，还是选择一个固定模型做三方案消融，避免“模型差异”和“反馈/记忆差异”混淆。建议先选一个模型完成消融，再扩展到跨模型重复。
3. **记忆训练协议：** 确认是否接受“训练 seeds 写入、冻结 eval seeds 只读”的设计；这是避免评测泄漏的必要条件。
4. **密钥轮换：** `agents.yaml` 当前含有明文 API key。应在任何真实运行前完成供应商侧轮换，并只使用环境变量注入。
