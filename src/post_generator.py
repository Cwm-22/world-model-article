"""知识星球风格推文生成模块。

严格遵循用户指定的固定结构模板，由 GLM 负责文案撰写，
程序负责结构拼接与占位符填充，确保格式 100% 一致。

设计要点（v3 简化版）：
- 文案只让 GLM 写：标题/简介/痛点/方法/落地/关键词；
- "作者单位 / 项目主页 / GitHub 仓库"三件套**不再联网抓取**，
  统一生成「（请手动填写）」占位，由使用者自行补全；
- 三件套结构仍然保留，保持推文模板完整。
"""
from __future__ import annotations

from typing import Any

from .llm_client import LLMClient

SYSTEM_PROMPT = """你是资深的 AI 技术内容创作者，擅长把前沿论文转化为
"知识星球"风格的中文推文：专业、信息密度高、痛点前置、突出量化数据，
但语言要兼顾通俗——把技术讲清楚，不堆砌术语。

你会收到论文标题、摘要、作者、arXiv 链接。你必须严格输出 JSON，
**不要**输出任何 markdown 代码块或额外说明。

字段定义如下：
{
  "short_name": "论文中文简称(8-15字，用于标题)",
  "headline": "核心亮点一句话概括(20-35字,陈述句,务必带量化数据)",
  "summary": "3-4句话总评：行业痛点 + 核心方案 + 量化效果 + 落地场景",
  "pain_point": "行业痛点拆解(1段,通俗讲清楚问题)",
  "core_method": "核心技术方案(1段,讲清楚方法,适当通俗)",
  "experiment_value": "实验效果与落地价值(1段,突出量化数据)",
  "tags": ["标签1","标签2","标签3","标签4","标签5"]
}

写作硬性要求：
1. 在 summary / pain_point / core_method / experiment_value 四个正文字段中，
   对**关键量化数字**、**核心技术名词**、**产品/系统名称**用 markdown 加粗
   （即在词或数字两侧加 ** ，例如 "成功率从 23.75% 升至 **66.25%**"，
   "提出 **潜空间扩散模型**"）。每个字段加粗 2-5 处，不要过度加粗。
2. 信息密度高但不晦涩，痛点前置。
3. 不要捏造数据——拿不准的数据点写"等具体数据见论文"。
"""


def _build_user_prompt(paper: dict[str, Any]) -> str:
    """组装推文生成的用户输入。"""
    authors = ", ".join(paper.get("authors", [])[:8]) or "未知"
    venue = paper.get("venue") or ""
    cite = paper.get("citation_count") or 0
    signals = []
    if venue:
        signals.append(f"发表会议/场所: {venue}")
    if cite:
        signals.append(f"被引数: {cite}")
    signals_block = "\n".join(signals) if signals else ""
    return (
        f"标题: {paper.get('title', '')}\n"
        f"作者: {authors}\n"
        f"arXiv: {paper.get('arxiv_url', '')}\n\n"
        f"摘要:\n{paper.get('summary', '')}\n\n"
        + (f"【可选信号】\n{signals_block}\n\n" if signals_block else "")
        + "请按系统提示输出 JSON。提醒：summary 必须包含量化效果数据；"
        "正文四个字段必须按规则做关键名词/数字的 markdown 加粗。"
    )


def _safe(value: Any) -> str:
    """空值显示为占位。"""
    s = str(value or "").strip()
    return s if s else "（请手动填写）"


def _format_post(paper: dict[str, Any], g: dict[str, Any]) -> str:
    """根据模板与生成内容拼接最终推文。

    "单位 / 项目主页 / GitHub 仓库"统一占位为「（请手动填写）」，
    由使用者自行补全，不再联网抓取。
    """
    short_name = _safe(g.get("short_name"))
    headline = _safe(g.get("headline"))
    summary = _safe(g.get("summary"))
    pain = _safe(g.get("pain_point"))
    method = _safe(g.get("core_method"))
    value = _safe(g.get("experiment_value"))

    tags = g.get("tags") or []
    tags = [f"#{t.strip().lstrip('#')}" for t in tags if str(t).strip()][:5]
    while len(tags) < 5:
        tags.append("#世界模型")

    full_title = paper.get("title", "")
    arxiv_url = paper.get("arxiv_url", "") or (
        f"https://arxiv.org/abs/{paper.get('arxiv_id', '')}".rstrip("/abs/")
        if paper.get("arxiv_id") else ""
    )

    return (
        f"# {short_name}｜{headline}\n\n"
        f"【总结】{summary}\n\n"
        f"单位：（请手动填写）\n"
        f"注：论文已上传星球，加入可一键下载阅读！\n\n"
        f"【简介】\n"
        f"{pain}\n\n"
        f"{method}\n\n"
        f"{value}\n\n"
        f"**《{full_title}》**\n\n"
        f"项目主页：（请手动填写）\n"
        f"GitHub仓库：（请手动填写）\n"
        f"论文arXiv链接：{arxiv_url}\n\n"
        f"关键词：{' '.join(tags)}\n"
    )


def generate_post(llm: LLMClient, paper: dict[str, Any]) -> str:
    """为单篇论文生成完整推文（Markdown 字符串）。

    流程：直接喂论文信息给 GLM 写文案 → 用固定模板拼接 → 输出。
    **不再联网抓取单位/链接**，相关字段由使用者手动补全。
    """
    payload = llm.chat_json(
        system=SYSTEM_PROMPT,
        user=_build_user_prompt(paper),
        temperature=0.6,
    )
    return _format_post(paper, payload)


def generate_top_posts(
    llm: LLMClient, top_papers: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """为 Top 论文逐一生成推文。

    Returns:
        (推文列表, 论文列表) — 这里没有联网增强，第二项就是输入原样
        的轻拷贝（保留接口签名以便日后扩展）
    """
    posts: list[str] = []
    enriched_list: list[dict[str, Any]] = []
    for i, p in enumerate(top_papers, 1):
        print(f"[post] ({i}/{len(top_papers)}) {p.get('arxiv_id')} ...")
        try:
            posts.append(generate_post(llm, p))
            enriched_list.append(dict(p))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 推文生成失败 {p.get('arxiv_id')}: {e}")
            posts.append(
                f"# {p.get('title', '')[:30]}…\n\n（推文生成失败：{e}）\n"
            )
            enriched_list.append(dict(p))
    return posts, enriched_list


# 兼容旧调用签名
def generate_post_text(llm: LLMClient, paper: dict[str, Any]) -> str:
    """旧名兼容。"""
    return generate_post(llm, paper)