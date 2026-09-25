# Uni API 本地预检与真实测试准备（2026-09-23）

根目录 `.env` 加载成功。`CSTCLOUD_API_KEY` 与 `DOUDIZHU_CREDENTIAL_MASTER_KEY` 均在本地可用，10 组模型配置均能解析凭据。配置 API 不返回密钥，内存 SQLite 中的凭据加密及读取通过。没有验证远端鉴权或模型可用性，没有真实模型调用。

本轮沿用 `/home/guozy/miniconda3/envs/doudizhu-arena` 和原未提交工作区。当天备份已存在：`/home/guozy/.codex/backups/doudizhu-arena/daily-2026-09-23/project`，189 MB。没有修改 `.env`、打开正式数据库、删除文件或还原原有改动。

## 可重复的本地预检

在项目根目录执行：

```bash
/home/guozy/miniconda3/envs/doudizhu-arena/bin/python -I -B scripts/preflight_uni_api.py
```

脚本按现有后端规则加载环境（进程变量优先，其次 `src/backend/.env`，最后根 `.env`），把 YAML 配置加载到内存数据库，通过实际配置 API 检查凭据状态。脚本禁止 socket 连接，不启动服务或比赛；异常详情和完整 API 响应不输出。凭据仅报告布尔状态。

- 退出 0 表示所请求的本地检查通过，**不表示远端已接受密钥**。
- `--output <新文件路径>` 保存脱敏 JSON，拒绝覆盖已有文件。
- `--spec <实验 YAML>` 额外调用进程内实验预检 API，要求完整预算与计价；失败退出 1。仍不会创建运行计划或发起推理。
- 脚本检查 YAML 加载到新内存库的结果，不检查已运行服务的旧进程环境或正式库中的历史配置；修改 `.env` 后仍需重启后端。

当前结果：[configuration.json](reviews/20260923-uni-preflight/configuration.json)。完整真实实验预检尚未通过，因为模型选择、预算和账户价格未确认。

## 本轮修改和验证

1. 真实实验模板不再沿用 mock 的 2 秒决策 / 60 秒任务超时。采用引擎默认叫分 60 秒、出牌 360 秒、团队时间池 3600 秒、耗尽后单次 60 秒；每任务总限时 3600 秒。决策限时包含重试。首次实测后再依据延迟调整，当前不是性能保证。
2. 真实模板移除 mock 的 `$0.01` 预算和 `mock-zero-v1` 计价标签，预算留空，由使用者填写。三组 1 / 3 / 3 次生成协议不变。
3. 配置页显示“已配置 / 未配置 / 无需密钥”；已配置表示本地存在凭据。编辑时密码框保持空白。
4. 新建真实实验页面显示决策和任务限时，便于运行前审阅。

本轮 **34 项后端针对性回归、7 项前端测试、生产构建通过**。后端覆盖新增的 10 项预检用例、已有 23 项网关契约和 1 项模板回归；网络连接尝试为 0。最初沙箱禁止创建测试套接字，退出沙箱后在脚本联网拦截仍启用的情况下通过。没有重复上一轮完整浏览器 mock 流程。

此前 338 项全量通过仍是上一版本的验收证据；本轮只对新增及相关路径做了回归，不能写成新版本已全量跑完。[本轮摘要](reviews/20260923-uni-preflight/validation-summary.json)、[JUnit](reviews/20260923-uni-preflight/backend.xml)。

## 可审阅的真实调用方案（未执行）

建议先做一次连通性检查，再决定是否执行三组 smoke。`qwen3.5` 是建议的首选，尚未由用户选定。

| 项目 | 单次连通性检查草案 | 三组 smoke 草案 |
| --- | --- | --- |
| 网关 | `https://uni-api.cstcloud.cn/v1` | 同左 |
| 协议 | `POST /chat/completions`，OpenAI 兼容 | 同左 |
| 建议配置 / 模型 | `Qwen-Balanced` / `qwen3.5` | 同左 |
| 规模 | 1 次请求，失败不自动重试 | 1 seed × 3 变体 × 每场 1 手，共 3 任务 / 6 桌手 |
| 输出上限 | 512 token | 每次 512 token |
| 采样 | temperature=0，top_p=1，不传 seed | 同左 |
| 输入占额 | 建议 2048 token | 每次 65536 token 的保守占额 |
| 调用上限 | 1 | 2000（停止上限，不是预期用量） |
| 预算、单价、来源和生效日期 | 待确认 | 待确认 |

单次检查拟发送 system=`Return only valid JSON.`，user=`Reply with {"ok":true}.`。拟设请求总限时 180 秒，SDK 自动重试为 0。响应只检查非空文本、实际返回模型及 usage；短输出截断或 reasoning 消耗也需保留为实测结果，不能假装零成本。此处是待实施的调用规格，不是已经运行的脚本。

设输入单价为 `P_in`、输出单价为 `P_out`，单位均为 USD / 百万 token：

- 单次检查建议占额：`(2048 × P_in + 512 × P_out) / 1,000,000`。
- smoke 每次调用占额：`(65536 × P_in + 512 × P_out) / 1,000,000`。两桌可能同时占额，需将其计入预算。
- 实际已知费用按返回 usage 结算；usage 缺失保留占额，不能记作免费。网关是否另计 reasoning、缓存或使用其他币种，须以账户实际规则核对。

没有把模型厂商公开标价当作 Uni API 账户价格，也没有填入测试用假价格。需提供选定模型、最高预算、输入/输出单价、币种、计价来源与生效日期，才能核算数字费用并形成可运行规格。

[smoke 草案](../src/backend/evaluation/experiments/uni-api-smoke-draft.yaml) 故意保留空预算和空计价，当前预检必须拒绝执行；[拒绝结果](reviews/20260923-uni-preflight/draft-preflight.json)。确认上述信息并完成首次连通性检查后，再创建新 manifest。后端源码本轮已改变，旧 manifest 不能继续运行，不能修改旧哈希绕过校验。
