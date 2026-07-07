# 世界模型论文监控智能体 🌍📄

> 多源聚合检索世界模型方向论文 → GLM 多维质量评估 → 联网抓取真实单位/链接生成知识星球风格推文，全流程一键运行，并支持 GitHub Actions 云端每日定时。

---

## ✨ 功能特性

| 模块 | 说明 |
| --- | --- |
| 🔎 **多源聚合检索** | 同时调用 arXiv、HuggingFace Daily Papers、OpenReview、Semantic Scholar 四个数据源，跨源去重；关键词已放宽到 26 个，覆盖世界模型大领域 |
| 🧠 质量评估 | GLM 从 5 维度加权打分（创新 30 / 权威 20 / 关联 20 / 落地 20 / 资源 10，满分 100）。venue、被引数、社区热度、代码仓库等**多源聚合元信息**会作为打分参考注入 |
| ✍️ 推文生成 | 严格遵循知识星球模板，**对关键数字/技术名词/系统名做 markdown 加粗**，痛点前置、突出量化数据、信息密度高 |
| 🌐 真实元信息 | 生成推文前**联网抓取 arXiv PDF 首页**，提取作者单位、项目主页、GitHub 链接；单位缺失也无"见论文"等偷懒话术，一律如实填"暂无" |
| 💾 输出存档 | `output/` 下生成 `每日论文汇总表.md`（含 venue / 被引 / 来源三列）与 `top3_推文.md` |
| 🔁 去重 | `data/processed_papers.json` 记录已处理论文，跨天不重复 |
| ☁️ 云端定时 | 内置 GitHub Actions 工作流，每日自动运行并把日报提交回仓库 |

---

## 📁 项目结构

```
world_model_article/
├── main.py                    # 主入口（run-once / 本地定时）
├── regen_posts.py             # 一次性脚本：对已知 Top N 重新生成推文
├── requirements.txt
├── .env.example               # 环境变量模板
├── .gitignore
├── README.md
├── src/
│   ├── __init__.py
│   ├── config.py              # 配置加载（环境变量 + .env，含多源开关）
│   ├── llm_client.py          # GLM/OpenAI 兼容客户端（JSON 稳健抽取）
│   ├── source_aggregator.py   # 多源检索聚合：去重 + S2 富化 + 截断
│   ├── storage.py             # 去重与本地存储
│   ├── paper_evaluator.py     # 5 维度打分评估（含 venue/citation 参考输入）
│   ├── post_generator.py      # 知识星球推文生成（加粗 + 真实元信息）
│   ├── paper_meta.py          # 联网抓 arXiv PDF/HTML，提取单位/项目/代码
│   ├── reporter.py            # Markdown 报告输出（含 venue/被引/来源三列）
│   ├── arxiv_search.py        # arXiv 检索底层（被 ArxivSource 复用）
│   └── sources/               # 多数据源子包
│       ├── base.py            # 标准化、跨源去重工具
│       ├── arxiv_source.py    # arXiv 源
│       ├── hf_daily_source.py # HuggingFace Daily Papers 源
│       ├── openreview_source.py # OpenReview 源（顶会稿件）
│       └── semantic_scholar.py # Semantic Scholar（关键词检索 + arXiv id 富化）
├── data/                      # processed_papers.json（运行时生成）
├── output/                    # 每日汇总表与推文（运行时生成）
└── .github/workflows/daily.yml# GitHub Actions 每日定时
```

---

## 🚀 快速开始（本地）

### 1. 安装依赖

```bash
cd world_model_article
pip install -r requirements.txt
```

## 🔌 数据源

聚合器并行调用以下 4 个源，然后跨源去重，按提交日期倒序截断：

| 数据源 | 用途 | 独特价值 | 是否需要 Key |
| --- | --- | --- | --- |
| **arXiv** | cs.CV/cs.AI/cs.RO/cs.LG 分类 + 关键词检索，下载 PDF 提取单位/链接 | 覆盖最广、有官方 API | 否 |
| **HuggingFace Daily Papers** | 社区人工策展的每日精选论文 | `upvotes`（点赞热度信号）+ `githubRepo`（代码链接）+ `ai_keywords` | 否 |
| **OpenReview** | ICLR / NeurIPS / COLM 等顶会投稿 | `venue`（如 "ICLR 2026 Workshop World Models"）+ `keywords` | 否 |
| **Semantic Scholar** | 关键词检索 + 按 arXiv id 反查 | `venue`（顶会顶刊）+ `citationCount`（被引数）| 可选（无 Key 也可用，仅更易限流） |

> **OpenReview 在受限网络下可能 403**——抓不到不影响其它源，框架会自动跳过。可在 `.env` 用 `ENABLE_OPENREVIEW=0` 关闭。
> **Semantic Scholar 无 Key 会限流**（429 退避重试），申请入口：https://www.semanticscholar.org/product/api#api-key-form

**关键词设计**：已经放宽到 26 个语义子集，覆盖世界模型大领域，任一命中即纳入候选：
- 世界模型族（`world model / world models / world simulator / world simulation`）
- 生成式 3D / 场景（`3D scene generation / scene synthesis / generative 3D world / explorable world / 3D world modeling / scene completion`）
- 视频 / 未来预测（`video world model / video prediction / future prediction / action-conditioned video / dreamer`）
- 具身 / 机器人 / 仿真（`embodied world simulation / embodied world model / robot world model / interactive environment / physics-based simulation / robot learning simulator`）
- 前向动力学 / 潜空间（`forward dynamics / latent dynamics / planet model / recurrent state space model / action-conditioned prediction`）

如果你觉得候选太宽（每篇还要花一次评估开销），可在 `.env` 用 `SEARCH_KEYWORDS=...` 进一步收窄。

### 2. 配置 API Key

复制模板并填入你的智谱 GLM API Key：

```bash
cp .env.example .env
# 然后编辑 .env，把 GLM_API_KEY 改成你的真实 Key
```

### 3. 运行

```bash
# 单次运行完整流程
python main.py

# 调试模式：只检索+评估，不写文件、不登记已处理
python main.py --dry-run

# 本地定时：每天 09:30 自动执行
python main.py --schedule 09:30
```

运行结束后：

- `output/YYYY-MM-DD_每日论文汇总表.md` — 全部论文评分表 + Top 3
- `output/YYYY-MM-DD_top3_推文.md` — 可直接复制到知识星球的推文

---

## ☁️ GitHub Actions 云端每日定时

1. 把项目推到 GitHub 仓库
2. 进入仓库 **Settings → Secrets and variables → Actions**，新增 Secret：
   - `GLM_API_KEY` = 你的智谱 API Key（**必填**）
3. 默认每天 **北京时间 09:00** 自动运行；也可在
   **Actions → 世界模型论文日报 → Run workflow** 手动触发
4. 运行成功后，`output/` 与 `data/processed_papers.json` 会自动提交回仓库

> 想调整时间，编辑 `.github/workflows/daily.yml` 中的 `cron` 表达式即可（注意是 UTC 时间）。

---

## ⚙️ 可配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GLM_API_KEY` | — | **必填**，智谱 GLM API Key |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | GLM 接口地址 |
| `GLM_MODEL` | `glm-5.2` | 模型名 |
| `ARXIV_CATEGORIES` | `cs.CV,cs.AI,cs.RO,cs.LG` | arXiv 分类 |
| `SEARCH_KEYWORDS` | 26 个放宽关键词 | 检索关键词（任一命中即纳入） |
| `SINCE_DATE` | `20260101` | 仅保留此后提交的论文（YYYYMMDD） |
| `MAX_PAPERS_PER_DAY` | `20` | 聚合后每日拉取上限 |
| `TOP_K` | `3` | 选 Top 几篇生成推文 |
| `ENABLE_ARXIV` | `1` | 是否启用 arXiv 源 |
| `ENABLE_HF_DAILY` | `1` | 是否启用 HuggingFace Daily Papers |
| `ENABLE_OPENREVIEW` | `1` | 是否启用 OpenReview |
| `ENABLE_SEMANTIC_SCHOLAR` | `1` | 是否启用 Semantic Scholar |
| `HF_LOOKBACK_DAYS` | `14` | HF Daily 回看天数 |
| `OPENREVIEW_SINCE_YEAR` | `2024` | OpenReview 仅保留此年后稿件 |
| `SEMANTIC_SCHOLAR_SINCE_YEAR` | `2024` | S2 关键词检索下限年份 |
| `SEMANTIC_SCHOLAR_API_KEY` | — | 可选，提升 S2 限流配额 |

---

## 🧪 测试建议

- 先用 `python main.py --dry-run` 验证检索与打分链路是否通畅
- 单独测试检索：`python -m src.arxiv_search`
- 检查 `output/` 下生成的 Markdown 是否符合预期格式
