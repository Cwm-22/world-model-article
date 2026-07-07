"""知识星球风格推文生成模块。

严格遵循用户指定的固定结构模板，由 GLM 负责文案撰写，
程序负责结构拼接与占位符填充，确保格式 100% 一致。

升级点（相对初版）：
- 推文内容里对**核心关键词/数字/技术名词**加粗，提升可读性与信息密度；
- 生成前先调用 paper_meta.enrich_paper 联网抓取 arXiv 页面，
  把真实"作者单位 / 项目主页 / GitHub 仓库"喂给 LLM 抽取，
  无则一律填"暂无"，绝不再写"见论文"这类偷懒话术。
"""
from __future__ import annotations

import re
from typing import Any

from .llm_client import LLMClient
from .paper_meta import enrich_paper, summarize_for_prompt


def _join_inst(insts: list[str], venue: str = "") -> list[str]:
    """ institutions 列表与 venue 合并（venue 作为单位提示信息）。"""
    out_list = [x for x in insts if x and str(x).strip()
                and str(x).strip() not in ("暂无", "未知", "见论文")]
    return out_list

SYSTEM_PROMPT = """你是资深的 AI 技术内容创作者，擅长把前沿论文转化为
"知识星球"风格的中文推文：专业、信息密度高、痛点前置、突出量化数据，
但语言要兼顾通俗——把技术讲清楚，不堆砌术语。

我会给你论文信息，以及一段"联网从 arXiv 抓取到的论文首页原文片段"
（含真实作者单位与代码/项目链接）。
你必须严格输出 JSON，**不要**输出任何 markdown 代码块或额外说明。

字段定义如下：
{
  "short_name": "论文中文简称(8-15字，用于标题)",
  "headline": "核心亮点一句话概括(20-35字,陈述句,务必带量化数据)",
  "summary": "3-4句话总评：行业痛点 + 核心方案 + 量化效果 + 落地场景",
  "pain_point": "行业痛点拆解(1段,通俗讲清楚问题)",
  "core_method": "核心技术方案(1段,讲清楚方法,适当通俗)",
  "experiment_value": "实验效果与落地价值(1段,突出量化数据)",
  "institutions": ["机构1","机构2","机构3"],   // ⚠️ 必须从联网抓取到的原文片段里抽取真实单位；若有多校/多单位联合，最多列 4 个关键机构，写中文译名（专有名词可保留英文）；若原文片段里没有任何单位信息，才允许返回 ["暂无"]
  "tags": ["标签1","标签2","标签3","标签4","标签5"]
}

写作硬性要求：
1. 在 summary / pain_point / core_method / experiment_value 四个正文字段中，
   对**关键量化数字**、**核心技术名词**、**产品/系统名称**用 markdown 加粗
   （即在词或数字两侧加 ** ，例如 "成功率从 23.75% 升至 **66.25%**"，
   "提出 **潜空间扩散模型**"）。每个字段加粗 2-5 处，不要过度加粗。
2. 信息密度高但不晦涩，痛点前置。
3. institutions 严禁出现"见论文""见原文""未知"等偷懒话术；拿不到就返回 ["暂无"]。
4. 不要捏造单位或链接——拿不准的留空或写"暂无"。
5. 若原文片段未找到单位，但下文已给你 venue（如"ICLR 2026 Workshop"），
   institutions 可填 ["该论文见 venue"] 中的 venue 简称——但绝不可写"见论文"。
"""


def _build_user_prompt(paper: dict[str, Any]) -> str:
    """组装推文生成的用户输入（含联网真实元信息）。"""
    authors = ", ".join(paper.get("authors", [])[:8]) or "未知"
    meta_block = summarize_for_prompt(paper)
    # 多源聚合的额外信号：venue / 被引数 / 社区热度
    venue = paper.get("venue") or ""
    cite = paper.get("citation_count") or 0
    upvotes = paper.get("upvotes") or 0
    signals = []
    if venue:
        signals.append(f"发表会议/场所: {venue}")
    if cite:
        signals.append(f"被引数: {cite}")
    if upvotes:
        signals.append(f"社区热度(HF upvotes): {upvotes}")
    signals_block = "\n".join(signals) if signals else ""
    return (
        f"标题: {paper.get('title', '')}\n"
        f"作者: {authors}\n"
        f"arXiv: {paper.get('arxiv_url', '')}\n\n"
        f"摘要:\n{paper.get('summary', '')}\n\n"
        f"{meta_block}\n"
        + (f"【多源聚合的额外信号】\n{signals_block}\n\n" if signals_block else "")
        + "请按系统提示输出 JSON。提醒：summary 必须包含量化效果数据；"
        "正文四个字段必须按规则做关键名词/数字的 markdown 加粗。"
    )


def _safe(value: Any) -> str:
    """空值统一显示为占位。"""
    s = str(value or "").strip()
    return s if s else "（暂无）"


def _format_institutions(insts: Any) -> str:
    """格式化单位展示：合并为数、最多 4 个。"""
    if not insts:
        return "暂无"
    items = [str(x).strip() for x in insts if str(x).strip()
             and str(x).strip() not in ("暂无", "未知", "见论文")]
    if not items:
        return "暂无"
    items = items[:4]
    if len(items) >= 3:
        return "多单位联合：" + "·".join(items)
    return "·".join(items)


def _format_post(paper: dict[str, Any], g: dict[str, Any]) -> str:
    """根据模板与生成内容拼接最终推文。"""
    short_name = _safe(g.get("short_name"))
    headline = _safe(g.get("headline"))
    summary = _safe(g.get("summary"))
    institution = _format_institutions(g.get("institutions"))
    pain = _safe(g.get("pain_point"))
    method = _safe(g.get("core_method"))
    value = _safe(g.get("experiment_value"))

    # 链接：正则已抽到真实值就用真实值，否则填"暂无"（绝不留空写"见论文"）
    project = paper.get("project_page") or g.get("project_page") or ""
    github = paper.get("github") or g.get("github") or ""
    project_line = project if project else "暂无"
    github_line = github if github else "暂无"

    tags = g.get("tags") or []
    tags = [f"#{t.strip().lstrip('#')}" for t in tags if str(t).strip()][:5]
    while len(tags) < 5:
        tags.append("#世界模型")

    full_title = paper.get("title", "")
    arxiv_url = paper.get("arxiv_url", "")

    return (
        f"# {short_name}｜{headline}\n\n"
        f"【总结】{summary}\n\n"
        f"单位：{institution}\n"
        f"注：论文已上传星球，加入可一键下载阅读！\n\n"
        f"【简介】\n"
        f"{pain}\n\n"
        f"{method}\n\n"
        f"{value}\n\n"
        f"**《{full_title}》**\n\n"
        f"项目主页：{project_line}\n"
        f"GitHub仓库：{github_line}\n"
        f"论文arXiv链接：{arxiv_url}\n\n"
        f"关键词：{' '.join(tags)}\n"
    )


def generate_post(
    llm: LLMClient, paper: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """为单篇论文生成完整推文。

    流程：
    1. enrich_paper 联网抓取论文首页/HTML/PDF 原文 + 链接；
    2. 把原文片段与论文信息一起喂给 LLM 撰写文案并抽取单位；
    3. 用真实链接 + 模型文案拼接最终推文。

    Returns:
        (推文 markdown, 已联网增强的 paper dict)
    """
    enriched = enrich_paper(paper)
    payload = llm.chat_json(
        system=SYSTEM_PROMPT,
        user=_build_user_prompt(enriched),
        temperature=0.6,
    )
    # 让 LLM 写出的链接优先级低于正则抽取的真实链接
    post = _format_post(enriched, payload)
    return post, enriched


def generate_top_posts(
    llm: LLMClient, top_papers: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """为 Top 论文逐一生成推文。

    Returns:
        (推文列表, 联网增强后的论文列表)
    """
    posts: list[str] = []
    enriched_list: list[dict[str, Any]] = []
    for i, p in enumerate(top_papers, 1):
        print(f"[post] ({i}/{len(top_papers)}) {p.get('arxiv_id')} ...")
        try:
            post, enriched = generate_post(llm, p)
            posts.append(post)
            enriched_list.append(enriched)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 推文生成失败 {p.get('arxiv_id')}: {e}")
            posts.append(
                f"# {p.get('title', '')[:30]}…\n\n（推文生成失败：{e}）\n"
            )
            enriched_list.append(p)
    return posts, enriched_list


# 兼容旧调用签名
def generate_post_text(llm: LLMClient, paper: dict[str, Any]) -> str:
    post, _ = generate_post(llm, paper)
    return post