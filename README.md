# Doudizhu Arena 🃏

**AI 斗地主竞技场** — 使用大语言模型（LLM）作为 AI 代理，以"双人桥牌复制赛"格式进行斗地主比赛。

## 项目概述

Doudizhu Arena 是一个自动化斗地主 AI 对战平台。每场比赛采用**复制赛制（Duplicate Bridge 格式）**：同一手牌在 A/B 两张桌子上独立进行，通过差分计分（IMP 式，上限 12 分）比较两队表现，消除发牌运气的影响。

### 核心特性

- 🤖 **多 AI 对战**：支持 Claude 和 GPT 系列模型的 AI 代理，可自定义策略
- 🏟️ **复制赛制**：AB 双桌同步进行，消除运气因素
- 📊 **实时观战**：WebSocket 实时推送每步出牌、叫牌、思考过程
- 🧠 **代理记忆**：AI 在每局后反思，每场比赛后总结，积累长期记忆
- ⏱️ **超时控制**：叫牌/出牌分别限制、团队时间池、LLM 失败自动降级
- ⏯️ **暂停/恢复**：比赛中可随时暂停和恢复
- 🎮 **Web 界面**：Vue 3 前端——创建比赛、实时观战、回放历史

---

## 系统架构

```
┌──────────────────────────────────────────────────┐
│                    Frontend (Vue 3)               │
│         Port 5173 (dev) / Port 80 (nginx)         │
└──────────────────────┬───────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼───────────────────────────┐
│              Backend (FastAPI)                    │
│         Port 8000 (uvicorn)                      │
│                                                   │
│  ┌─────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  Arena  │  │ Tournament│  │    Engine      │  │
│  │  Agent  │  │  Runner   │  │  Card/Rules    │  │
│  │  LLM    │  │  Seating  │  │  Scoring       │  │
│  │  Memory │  │  Scoring  │  │  Timeout/Pause │  │
│  └─────────┘  └──────────┘  └────────────────┘  │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  SQLite Database (data/arena.db)             │ │
│  │  13 tables — agents, matches, hands, plays  │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

### 目录结构

```
Doudizhu-Arena/
├── src/
│   ├── backend/                    # Python FastAPI 后端
│   │   ├── main.py                 # 应用入口
│   │   ├── arena/
│   │   │   ├── agent/              # AI 代理 (协议、LLM、随机、记忆、解析)
│   │   │   │   └── prompts/        # LLM 提示词模板 (叫牌/出牌/反思/总结)
│   │   │   ├── api/                # REST API + WebSocket
│   │   │   │   └── routes/         # match.py, replay.py, agent.py
│   │   │   ├── config/             # settings.py, agents.yaml
│   │   │   ├── db/                 # SQLite 持久化层
│   │   │   ├── engine/             # 斗地主游戏引擎 (牌/规则/状态/计分/超时/暂停)
│   │   │   ├── llm/                # LLM 供应商适配 (Claude, OpenAI)
│   │   │   └── tournament/         # 比赛编排 (比赛/牌桌运行器、座位、差分计分)
│   │   ├── tests/                  # 12 个测试文件
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── requirements.txt
│   └── frontend/                   # Vue 3 + Vite + Tailwind CSS
│       ├── src/
│       │   ├── views/              # 页面 (比赛列表、比赛视图、创建比赛、回放)
│       │   ├── components/         # 组件 (牌桌、手牌、出牌历史等)
│       │   ├── stores/             # Pinia 状态管理
│       │   ├── api/index.js        # REST + WebSocket 客户端
│       │   └── router/index.js
│       ├── dist/                   # 预构建的前端资源
│       ├── Dockerfile
│       ├── nginx.conf
│       └── package.json
├── docker-compose.yml              # 一键部署
├── .env.example                    # 环境变量模板
└── CLAUDE.md                       # Claude Code 工作规则
```

---

## 快速开始

### 前提条件

- **Python 3.12+**（项目使用 conda 环境 `doudizhu-arena`）
- **Node.js 22+**（前端开发需要）
- **Docker**（推荐部署方式）
- **Anthropic API Key** 和/或 **OpenAI API Key**（使用 AI 代理） 

### 方式一：Docker Compose（推荐）

最简单的一键启动方式：

```bash
# 1. 克隆项目后，配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入你的 API Keys:
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-...

# 2. 启动所有服务
docker compose up --build

# 3. 访问
#    前端: http://localhost
#    后端 API: http://localhost:8000
#    API 文档: http://localhost:8000/docs
```

### 方式二：本地开发运行

#### 后端

```bash
# 激活 conda 环境
conda activate doudizhu-arena

# 进入后端目录
cd src/backend

# 安装依赖
pip install -r requirements.txt

# 创建 data 目录（用于 SQLite 数据库）
mkdir -p ../../data

# 启动后端服务
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### 前端

```bash
# 进入前端目录
cd src/frontend

# 安装依赖
npm install

# 启动开发服务器（自动代理 /api 到 localhost:8000）
npm run dev
```

然后访问 http://localhost:5173

---

## 使用指南

### 1. 配置 AI 代理

代理定义在 [src/backend/arena/config/agents.yaml](src/backend/arena/config/agents.yaml) 中。启动时自动加载到数据库。

```yaml
agent_definitions:
  - name: "Aggressive-Claude"     # 代理名称
    provider: claude              # 供应商: claude 或 openai
    model: claude-opus-4-7        # 模型名称
    api_key: "${ANTHROPIC_API_KEY}"  # 环境变量引用
    system_prompt_override: null  # 可选的系统提示词覆盖

  - name: "Balanced-GPT"
    provider: openai
    model: gpt-4o
    api_key: "${OPENAI_API_KEY}"
    system_prompt_override: "你偏好稳健保守的打法。尽量保持牌型完整，避免冒进。"
```

你可以：
- 通过前端「代理管理」页面增删改代理
- 通过 API `/api/v1/agents` 管理代理
- 直接编辑 `agents.yaml` 后重启后端

### 2. 创建比赛

#### 通过 Web 界面

1. 打开 http://localhost:5173（开发）或 http://localhost（Docker）
2. 点击「创建比赛」
3. 填写比赛名称
4. 为红队和蓝队各选择 4 名代理
5. 配置比赛参数（局数、KO 开关、超时时间等）
6. 点击「创建」

#### 通过 API

```bash
# 1. 获取可用代理列表
curl http://localhost:8000/api/v1/agents

# 2. 创建比赛（需要 4 红 + 4 蓝代理的 ID）
curl -X POST http://localhost:8000/api/v1/matches \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Claude vs GPT 对决",
    "config": {
      "total_hands": 20,
      "ko_enabled": true
    },
    "team_red": {
      "name": "红队",
      "agents": ["<agent-id-1>", "<agent-id-2>", "<agent-id-3>", "<agent-id-4>"]
    },
    "team_blue": {
      "name": "蓝队",
      "agents": ["<agent-id-5>", "<agent-id-6>", "<agent-id-7>", "<agent-id-8>"]
    }
  }'

# 3. 启动比赛
curl -X POST http://localhost:8000/api/v1/matches/<match-id>/start
```

### 3. 观看比赛

比赛开始后，你可以：

- **实时观战**：在前端点击比赛进入实时视图，看到 AB 两桌的牌局、当前出牌、AI 思考过程
- **WebSocket**：直接连接 `ws://localhost:8000/ws/match/<match-id>` 获取实时事件流（15 种事件类型：hand_started, bidding_update, card_played, score_update, match_ended 等）
- **暂停/恢复**：在前端或通过 API 暂停和恢复比赛

### 4. 查看结果与回放

- **比赛列表**：前端首页显示所有比赛及状态
- **比赛详情**：点击比赛查看每局的差分得分、累计总分、KO 结果
- **回放模式**：点击「回放」逐局查看完整出牌历史和 AI 思考过程
- **API 查询**：
  ```bash
  # 获取比赛详情
  curl http://localhost:8000/api/v1/matches/<match-id>
  
  # 获取所有局列表
  curl http://localhost:8000/api/v1/matches/<match-id>/hands
  
  # 获取单局详情
  curl http://localhost:8000/api/v1/matches/<match-id>/hands/1
  ```

### 5. 运行测试

```bash
conda activate doudizhu-arena
cd src/backend

# 全部测试
pytest tests/ -v

# 快速冒烟测试
pytest -m smoke

# 慢速测试
pytest -m slow
```

---

## 比赛机制

### 复制赛制（Duplicate Bridge）

| 机制 | 说明 |
|------|------|
| **同牌分发** | A/B 两桌使用完全相同的发牌（种子控制） |
| **差分计分** | 每局计算两桌的得分差，上限 ±12 分 |
| **KO 规则** | 当一方领先超过剩余局数 × 12 分时，比赛提前结束 |
| **加时赛** | 20 局结束平局时，进入加时局直到分出胜负 |
| **座位轮换** | 4 座（南/东/北/西）轮换，每局一人轮空 |

### 斗地主规则

完整实现 15 种牌型识别和比较：
单张、对子、三条、三带一、三带二、顺子、连对、飞机、飞机带单、飞机带双、四带二、炸弹、火箭、四带二对、空过

### AI 代理工作流

每局比赛中，每个 AI 代理经历：
1. **叫牌阶段**：根据手牌决定是否叫地主
2. **出牌阶段**：每轮根据当前牌局选择出牌或过牌
3. **反思阶段**：每局结束后反思自己的决策
4. **总结阶段**：比赛结束后总结整场表现，更新长期记忆

---

## 配置参考

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | Anthropic API 密钥 | — |
| `OPENAI_API_KEY` | OpenAI API 密钥 | — |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `DATABASE_URL` | 数据库路径 | `sqlite:///data/arena.db` |
| `LOG_DIR` | 日志目录 | `src/backend/logs` |

### 比赛参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `total_hands` | 总局数 | 20 |
| `ko_enabled` | 是否启用 KO 规则 | true |
| `diff_cap` | 差分上限 | 12 |
| `bidding_timeout_seconds` | 叫牌超时（秒） | 60 |
| `individual_play_timeout_seconds` | 单次出牌超时（秒） | 360 |
| `team_pool_timeout_seconds` | 团队总时间池（秒） | 3600 |

---

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/health` | 健康检查 |
| `GET` | `/api/v1/agents` | 代理列表 |
| `POST` | `/api/v1/agents` | 创建代理 |
| `GET` | `/api/v1/matches` | 比赛列表（支持分页和状态过滤） |
| `POST` | `/api/v1/matches` | 创建比赛 |
| `GET` | `/api/v1/matches/{id}` | 比赛详情 |
| `POST` | `/api/v1/matches/{id}/start` | 启动比赛 |
| `POST` | `/api/v1/matches/{id}/pause` | 暂停比赛 |
| `POST` | `/api/v1/matches/{id}/resume` | 恢复比赛 |
| `DELETE` | `/api/v1/matches/{id}` | 删除比赛（仅已创建未开始） |
| `GET` | `/api/v1/matches/{id}/hands` | 局列表 |
| `GET` | `/api/v1/matches/{id}/hands/{num}` | 局详情 |
| `GET` | `/api/v1/matches/{id}/hands/{num}/table/{a\|b}` | 单桌详情 |
| `WS` | `/ws/match/{id}` | WebSocket 实时流 |

完整 API 文档（Swagger UI）：http://localhost:8000/docs

---

## 日志

日志文件位于 `src/backend/logs/`（Docker 中映射到 `/app/logs/`）：
- `app.log` — 应用日志
- `llm_calls.log` — LLM 调用记录（提示词、响应、耗时）
- `prompts.log` — 完整提示词内容

日志自动轮转（10MB/文件，保留 5 个备份）。

---

## License

MIT
