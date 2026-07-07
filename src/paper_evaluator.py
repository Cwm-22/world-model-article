"""论文质量评估模块。

5 维加权打分（总分 100）：
① 方法创新性 30  ② 作者单位权威性 20  ③ 世界模型主题关联度 20
④ 实验落地性 20  ⑤ 资源完整性 10

调用 GLM 对每篇论文输出严格 JSON，然后汇总排序、选出 Top K。
"""
from __future__ import annotations

from typing import Any

from .llm_client import LLMClient

# 维度 -> 满分，与 prompt 内说明保持一致
DIMENSIONS: dict[str, int] = {
    "method_innovation": 30,
    "author_authority": 20,
    "topic_relevance": 20,
    "experiment_feasibility": 20,
    "resource_completeness": 10,
}

SYSTEM_PROMPT = """你是一名严谨的 AI/计算机视觉领域资深审稿人，
专注于"世界模型 (World Model)"方向的研究评估。
你将收到一篇论文的标题、摘要、作者列表、arXiv 链接，
**以及一组多源聚合的元信息**（发表场所/会议、被引数、社区热度、代码仓库、数据来源），
需要从 5 个维度对该论文打分（必须是 0 到该维度上限之间的整数）：

1. method_innovation (方法创新性, 满分 30)
2. author_authority  (作者单位权威性, 满分 20) —— 多源元信息中的"发表场所"（如 NeurIPS/ICLR/CVPR 顶会）与"被引数"是重要参考依据
3. topic_relevance   (世界模型主题关联度, 满分 20)
4. experiment_feasibility (实验落地性, 满分 20) —— "数据来源"含 OpenReview/顶会稿件可作为审稿质量参考
5. resource_completeness  (资源完整性: 代码/项目页/数据, 满分 10) —— "代码仓库"字段直接决定本项分高低

请务必严格输出 JSON，**不要**输出任何额外文字、不要 markdown 代码块。
JSON 格式如下：
{
  "method_innovation": int,
  "author_authority": int,
  "topic_relevance": int,
  "experiment_feasibility": int,
  "resource_completeness": int,
  "one_line_comment": "一句话中文评价，专业、客观、有信息量"
}
"""


def _build_user_prompt(paper: dict[str, Any]) -> str:
    """组装单篇论文的用户输入（含多源元信息作为打分参考）。"""
    authors = ", ".join(paper.get("authors", [])[:10]) or "未知"
    venue = paper.get("venue") or ""
    citations = paper.get("citation_count") or 0
    upvotes = paper.get("upvotes") or 0
    github = paper.get("github") or ""
    sources = paper.get("sources") or []

    extra_lines: list[str] = []
    if venue:
        extra_lines.append(f"发表场所/会议: {venue}")
    if citations:
        extra_lines.append(f"被引数: {citations}")
    if upvotes:
        extra_lines.append(f"社区热度(HF upvotes): {upvotes}")
    if github:
        extra_lines.append(f"代码仓库: {github}")
    if sources:
        extra_lines.append(f"数据来源: {', '.join(sources)}")
    extra_block = "\n".join(extra_lines)

    base = (
        f"标题: {paper.get('title', '')}\n"
        f"作者: {authors}\n"
        f"arXiv: {paper.get('arxiv_url', '')}\n"
        f"提交日期: {paper.get('submitted_date', '')}\n\n"
        f"摘要:\n{paper.get('summary', '')}\n"
    )
    if extra_block:
        base += f"\n【多源元信息（评分参考）】\n{extra_block}\n"
    return base + "\n请按系统提示给出 JSON 评分。"


def _clamp(value: int, low: int, high: int) -> int:
    """把分数限制在合法区间。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return max(low, min(high, v))


def evaluate_one(llm: LLMClient, paper: dict[str, Any]) -> dict[str, Any]:
    """评估单篇论文，返回带 scores / total_score / comment 的增强 dict。"""
    result = llm.chat_json(
        system=SYSTEM_PROMPT,
        user=_build_user_prompt(paper),
        temperature=0.2,
    )

    scores = {
        k: _clamp(result.get(k, 0), 0, DIMENSIONS[k])
        for k in DIMENSIONS
    }
    total = sum(scores.values())
    comment = str(result.get("one_line_comment", "")).strip()

    enriched = dict(paper)
    enriched["scores"] = scores
    enriched["total_score"] = total
    enriched["comment"] = comment
    return enriched


def evaluate_papers(llm: LLMClient, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量评估并按总分降序排序。失败的论文给予保守 0 分但保留记录。"""
    evaluated: list[dict[str, Any]] = []
    for idx, p in enumerate(papers, 1):
        print(f"[eval] ({idx}/{len(papers)}) {p.get('arxiv_id')} ...")
        try:
            evaluated.append(evaluate_one(llm, p))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 评估失败 {p.get('arxiv_id')}: {e}")
            failure = dict(p)
            failure["scores"] = {k: 0 for k in DIMENSIONS}
            failure["total_score"] = 0
            failure["comment"] = "（评估失败，已跳过）"
            evaluated.append(failure)

    evaluated.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    return evaluated


def select_top_k(evaluated: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """选取前 K 篇（若可用论文不足 K，则返回实际数量）。"""
    return evaluated[: max(k, 0)]
