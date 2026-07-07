"""世界模型论文监控智能体 - 主入口。

完整流程：
  检索 arXiv -> 去重 -> GLM 评估打分 -> 选 Top 3 -> 生成推文 -> 落盘存档

用法：
    # 单次运行完整流程
    python main.py

    # 仅打印调试，不写文件
    python main.py --dry-run

    # 本地定时：每天 09:30 自动执行一次
    python main.py --schedule 09:30

    # 跳过拉取，直接对 processed_papers.json 复盘（可选）
环境变量：见 .env.example（GLM_API_KEY 必填）。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Any

from src.source_aggregator import fetch_by_ids, search_papers
from src.config import Config
from src.llm_client import LLMClient
from src.paper_evaluator import evaluate_papers, select_top_k
from src.post_generator import generate_top_posts
from src.reporter import build_posts_md, build_summary_md, save_text
from src.storage import (
    candidate_pool,
    filter_new,
    is_pushed,
    mark_evaluated,
    mark_pushed,
    pool_stats,
)


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")


def run_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """执行一次完整流水线，返回统计信息。"""
    _banner(f"🚀 世界模型论文日报 · 启动 {datetime.now():%Y-%m-%d %H:%M}")

    # 0. 校验配置
    Config.validate()

    # 1. 多源检索（arXiv + HF Daily + OpenReview + Semantic Scholar）
    enabled = [
        name for name, on in (
            ("arXiv", Config.enable_arxiv),
            ("HF Daily", Config.enable_hf_daily),
            ("OpenReview", Config.enable_openreview),
            ("Semantic Scholar", Config.enable_semantic_scholar),
        ) if on
    ]
    _banner(f"🔎 [1/5] 多源检索：{', '.join(enabled)}")
    papers = search_papers()
    print(f"   聚合后候选 {len(papers)} 篇")
    if papers:
        src_counter: dict[str, int] = {}
        for p in papers:
            for s in (p.get("sources") or [p.get("source", "?")]):
                src_counter[s] = src_counter.get(s, 0) + 1
        print("   各源贡献：", src_counter)

    # 2. 过滤掉已评估过的论文（避免重复花 GLM token）
    new_papers = filter_new(papers)
    pool = pool_stats()
    print(f"   今日新增候选 {len(new_papers)} 篇 ｜ "
          f"候选池：已评估 {pool['evaluated']}，已推送 {pool['pushed']}，"
          f"待选 {pool['candidates']}")

    # 3. 评估今日新论文打分（候选池历史论文分数已记录，不再重打）
    evaluated: list[dict[str, Any]] = []
    if new_papers:
        _banner("🧠 [2/5] GLM 质量评估打分（仅今日新论文）")
        llm = LLMClient()
        evaluated = evaluate_papers(llm, new_papers)

    # 4. 把今日新评估的入库（带分数），候选池随即可查
    if evaluated and not dry_run:
        print("📝 [登记评估结果] 写入候选池")
        for p in evaluated:
            mark_evaluated(
                p["arxiv_id"],
                title=p.get("title", ""),
                score=p.get("total_score"),
                scores=p.get("scores"),
                comment=p.get("comment", ""),
                venue=p.get("venue", ""),
                sources=p.get("sources") or [p.get("source", "")],
            )

    # 5. 从候选池全集里选 Top K（含今日新增 + 历史未推送）
    _banner(f"🏆 [3/5] 从候选池选 Top {Config.top_k}")
    candidates = candidate_pool(max_age_days=Config.candidate_max_age_days)
    if not candidates:
        _banner("✅ 候选池为空，流程结束。")
        return {
            "fetched": len(papers), "new": len(new_papers),
            "evaluated": len(evaluated), "candidates": 0, "posts": 0,
        }
    print(f"   候选池总 {len(candidates)} 篇（已评估未推送），按分数降序前 10：")
    for c in candidates[:10]:
        print(f"   - [{c.get('score', 0)}分] "
              f"{'🆕' if 'today' in (c.get('evaluated_at','') or '') else '  '} "
              f"{c.get('arxiv_id')} {c.get('title', '')[:50]}")
    # 标记今日新增进入 candidates 后是否被选中（用 arxiv_id 集合判断）
    today_ids = {p["arxiv_id"] for p in evaluated}
    selected = candidates[: Config.top_k]
    fresh_count = sum(1 for s in selected if s["arxiv_id"] in today_ids)
    print(f"   Top{len(selected)} 选中：今日新增占 {fresh_count}/{len(selected)}")

    if dry_run:
        _banner("🧪 dry-run 模式：不写文件、不标记已推送。")
        return {
            "fetched": len(papers),
            "new": len(new_papers),
            "evaluated": len(evaluated),
            "candidates": len(candidates),
            "posts": 0,
        }

    # 6. 对 Top 论文拉取完整信息（候选池里存的是评分原始数据，需要补完整
    #     作者/摘要等字段给推文模块用），并生成推文
    _banner(f"✍️  [4/5] 生成 Top {len(selected)} 推文（含联网元信息增强）")
    llm = llm if new_papers else LLMClient()  # 复用上半场 LLM 客户端
    selected_ids = [s["arxiv_id"] for s in selected]
    full_papers = fetch_by_ids(selected_ids)
    # 补分（fetch_by_ids 取回的论文无 score）
    score_map = {s["arxiv_id"]: s.get("score", 0) for s in selected}
    for p in full_papers:
        p["total_score"] = score_map.get(p.get("arxiv_id", ""), 0)
    if not full_papers:
        # 候选池论文没 arXiv 详情就退化使用候选池信息
        full_papers = list(selected)
    posts, top_enriched = generate_top_posts(llm, full_papers)

    # 7. 落盘
    _banner("💾 [5/5] 保存报告")
    sum_name, sum_md = build_summary_md(
        today_evaluated=evaluated,
        candidates=candidates,
        top_k=Config.top_k,
    )
    post_name, post_md = build_posts_md(posts)
    sum_path = save_text(sum_name, sum_md)
    post_path = save_text(post_name, post_md)
    print(f"   汇总表: {sum_path}")
    print(f"   推文:   {post_path}")

    # 8. 把进推文的 Top K 标记为"已推送"，从候选池移除
    pushed_ids = [p["arxiv_id"] for p in top_enriched]
    mark_pushed(pushed_ids, post_filename=post_name)
    print(f"   已标记推送：{pushed_ids}")

    _banner(
        f"🎉 完成！新增评估 {len(evaluated)} 篇，候选池总 "
        f"{len(candidates)} 篇，生成 {len(posts)} 篇推文。"
    )
    return {
        "fetched": len(papers),
        "new": len(new_papers),
        "evaluated": len(evaluated),
        "candidates": len(candidates),
        "posts": len(posts),
        "summary_path": str(sum_path),
        "posts_path": str(post_path),
    }


def run_scheduled(run_at: str) -> None:
    """本地定时调度：每天 run_at（HH:MM）执行一次。"""
    import schedule  # 延迟导入，避免单次运行也要装 schedule

    print(f"⏰ 已开启定时任务，每天 {run_at} 执行。Ctrl+C 退出。")
    schedule.every().day.at(run_at).do(run_pipeline)

    # 启动时先跑一次（可选，便于即时验证）
    run_pipeline()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n已停止定时任务。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="世界模型论文监控 + 评估 + 推文生成"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检索与评估，不写文件、不登记已处理",
    )
    parser.add_argument(
        "--schedule",
        metavar="HH:MM",
        help="启用本地定时调度，每天指定时间运行（如 09:30）",
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled(args.schedule)
    else:
        stats = run_pipeline(dry_run=args.dry_run)
        # 流程正常但无新增论文也算成功
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
