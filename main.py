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
    is_evaluated,
    is_pushed,
    mark_evaluated,
    mark_pushed,
    pool_stats,
    prune_pool,
    remove_from_pool,
)


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")


def _eval_and_register(llm: LLMClient,
                       to_eval: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """评估一批论文，写入候选池（带分数），返回带分数的 evaluated 列表。"""
    evaluated = evaluate_papers(llm, to_eval)
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
    return evaluated


def _top1_ensure_loop(
    llm: LLMClient,
    candidates: list[dict[str, Any]],
    new_papers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Top1 评分保障循环。

    只要候选池最高分 <= top1_min_score：
      1) 从候选池**彻底删除**当前评分最低的一篇（remove_from_pool）
      2) 从 new_papers 里取一篇**尚未评估**的论文，评估并入库
      3) 刷新候选池
    直到 Top1 > top1_min_score 或没有未评估论文 / 达到最大尝试次数。

    Returns:
        (最终候选池列表, 本次新评估的论文列表)
    """
    min_score = Config.top1_min_score
    max_attempts = Config.top1_max_attempts
    newly_evaluated: list[dict[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        if not candidates:
            print(f"   [换血 {attempt}] 候选池空，停止")
            break
        top1 = int(candidates[0].get("score", 0) or 0)
        if top1 > min_score:
            print(f"   [换血 {attempt}] Top1={top1} > {min_score}，达标准备")
            break

        lowest = candidates[-1]
        _banner(f"🔁 [换血第 {attempt}/{max_attempts} 次] Top1={top1} ≤ {min_score}，启动换血")
        print(f"   ① 删除最低分：[{lowest.get('score',0)}分] "
              f"{lowest.get('arxiv_id')} {lowest.get('title','')[:40]}")
        ok = remove_from_pool(lowest["arxiv_id"])
        if not ok:
            print("   ⚠ 删除失败（可能已推送），停止换血")
            break

        # 找一篇未评估的新论文
        pending = [p for p in new_papers
                   if p.get("arxiv_id")
                   and not is_evaluated(p["arxiv_id"])
                   and not is_pushed(p["arxiv_id"])]
        if not pending:
            print("   ⚠ 已无未评估论文可补，停止换血")
            candidates = candidate_pool()
            break

        next_paper = pending[0]
        print(f"   ② 评估补位：{next_paper.get('arxiv_id')} "
              f"{next_paper.get('title','')[:40]}")
        evaled = _eval_and_register(llm, [next_paper])
        newly_evaluated.extend(evaled)
        # 检索后再按 max_size 收紧
        prune_pool(min_score=0, min_remove=0, max_size=Config.pool_max_size)
        candidates = candidate_pool()

    # 跨循环汇总结果
    return candidates, newly_evaluated


def run_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """执行一次完整流水线，返回统计信息。"""
    _banner(f"🚀 世界模型论文日报 · 启动 {datetime.now():%Y-%m-%d %H:%M}")

    # 0. 校验配置
    Config.validate()

    # 0.5 每日检索之前：清理候选池
    #   - 剔除 score < PRUNE_MIN_SCORE 的候选
    #   - 若剔除数 < PRUNE_MIN_REMOVE，继续从最低分候选往下剔，至少剔 5 篇
    #   - 池上限 POOL_MAX_SIZE，超出再按低分往下剔
    _banner(
        f"🧹 [0/6] 清理候选池：< {Config.prune_min_score} 分剔除"
        f"（至少剔 {Config.prune_min_remove} 篇），池上限 {Config.pool_max_size}"
    )
    pruned = prune_pool(
        min_score=Config.prune_min_score,
        min_remove=Config.prune_min_remove,
        max_size=Config.pool_max_size,
    )
    print(
        f"   剔除 {pruned['removed_count']} 篇 "
        f"(低分 {pruned['removed_low_score']} + 兜底 {pruned['removed_floor']}"
        f" + 超额 {pruned['removed_overflow']})"
        f"，剩余候选 {pruned['remaining_count']} 篇"
    )
    if pruned["removed_ids"]:
        print(f"   剔除列表：{pruned['removed_ids'][:10]}{' ...' if len(pruned['removed_ids'])>10 else ''}")

    # 1. 多源检索（arXiv + HF Daily + OpenReview + Semantic Scholar）
    enabled = [
        name for name, on in (
            ("arXiv", Config.enable_arxiv),
            ("HF Daily", Config.enable_hf_daily),
            ("OpenReview", Config.enable_openreview),
            ("Semantic Scholar", Config.enable_semantic_scholar),
        ) if on
    ]
    _banner(f"🔎 [1/6] 多源检索：{', '.join(enabled)}")
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

    # 3. 评估今日新论文打分（首批评估 N 篇，留出后备给换血）
    evaluated: list[dict[str, Any]] = []
    if new_papers:
        _banner(
            f"🧠 [2/6] GLM 质量评估打分"
            f"（首批 {min(Config.evaluate_batch_size, len(new_papers))} / "
            f"{len(new_papers)} 篇）"
        )
        llm = LLMClient()
        if not dry_run:
            first_batch = new_papers[: Config.evaluate_batch_size]
            evaluated = _eval_and_register(llm, first_batch)
            # 检索后按 max_size 收紧池
            re_prune = prune_pool(
                min_score=0, min_remove=0,
                max_size=Config.pool_max_size,
            )
            if re_prune["removed_count"]:
                print(f"   池超限，再剔 {re_prune['removed_count']} 篇最低分")
            else:
                print(f"   池规模 {re_prune['remaining_count']}，未超 {Config.pool_max_size}")
        else:
            evaluated = evaluate_papers(llm, new_papers)

    # 5. 从候选池全集里选 Top K（含今日新增 + 历史未推送）
    _banner(f"🏆 [3/6] 从候选池选 Top {Config.top_k}")
    candidates = candidate_pool()
    if not candidates:
        _banner("✅ 候选池为空，流程结束。")  # noqa: E501
        return {
            "fetched": len(papers), "new": len(new_papers),
            "evaluated": len(evaluated), "candidates": 0, "posts": 0,
        }
    print(f"   候选池总 {len(candidates)} 篇（已评估未推送），按分数降序前 10：")
    for c in candidates[:10]:
        print(f"   - [{c.get('score', 0)}分] "
              f"{'🆕' if 'today' in (c.get('evaluated_at','') or '') else '  '} "
              f"{c.get('arxiv_id')} {c.get('title', '')[:50]}")

    # Top1 评分保障循环（score 必须严格大于 top1_min_score）
    if not dry_run and candidates:
        top1 = int(candidates[0].get("score", 0) or 0)
        _banner(
            f"🔥 保障 Top1 > {Config.top1_min_score}（当前 Top1 = {top1}）"
        )
        candidates, extra = _top1_ensure_loop(llm, candidates, new_papers)
        evaluated.extend(extra)
        if candidates:
            print(
                f"   换血后 Top1 = "
                f"{candidates[0].get('score', 0)}"
                f"，候选池共 {len(candidates)} 篇"
            )
        else:
            _banner("⚠ 换血后候选池为空，流程结束。")
            return {
                "fetched": len(papers), "new": len(new_papers),
                "evaluated": len(evaluated), "candidates": 0, "posts": 0,
            }

    # 今日新增计数（含换血补充评估的）
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
    _banner(f"✍️  [4/6] 生成 Top {len(selected)} 推文")
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
    _banner("💾 [5/6] 保存报告")
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
