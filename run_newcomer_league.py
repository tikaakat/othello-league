"""
新人リーグ（AM実行）：その日投稿されたキャラクリエイトのリクエストを集めて、
スイス方式のミニリーグ（1人あたり15局）で競わせる。上位者（前回のシーズンで
Dリーグに新規参入した人数分）だけが、この日の夜に実行される本戦でDリーグへ
新規参入する。

投稿が目標人数（30名）に満たない場合は、自動生成の候補で埋める。
本戦（run_season.py）とは別プロセス・別スケジュール（AM実行）で動かす想定。
"""
import argparse
import json
import os
import random

from othello_league.individual import LeagueIndividual
from othello_league.league import _build_character_creation_individual
from othello_league.swiss import run_swiss_league
from othello_league.names import NameRegistry
from othello_league.io_utils import load_season_state, save_season_state

NEWCOMER_LEAGUE_DEPTH = 3
NEWCOMER_LEAGUE_ROUNDS = 15
NEWCOMER_TARGET_POOL = 30
NEWCOMER_SUBMISSION_CAP = 40
CHARACTER_TYPES = ["balanced", "aggressive", "defensive", "corner"]


def _fill_with_auto_generated(entries, target, registry):
    """投稿が目標人数に満たない場合、自動生成の候補で埋める"""
    filled = list(entries)
    while len(filled) < target:
        filled.append({
            "name": registry.generate(),
            "type": random.choice(CHARACTER_TYPES),
            "auto_generated": True,
        })
    return filled


def run_newcomer_league(submissions, slots_needed, registry, depth=NEWCOMER_LEAGUE_DEPTH,
                         rounds=NEWCOMER_LEAGUE_ROUNDS, target_pool=NEWCOMER_TARGET_POOL):
    """
    submissions: [{"name":, "type":, "params":}, ...]（当日投稿分、上限は呼び出し側で適用済み想定）
    slots_needed: 今夜のDリーグ新規参入枠（前回シーズンの新規参入人数）
    戻り値: (winner_entries, standings, match_log)
      winner_entries: 勝者の元の投稿データ（{"name":,"type":,"params":}形式）のリスト
      standings: 順位表（表示用）
      match_log: 対局ログ（表示用）
    """
    entries = _fill_with_auto_generated(submissions, target_pool, registry)

    candidates = []
    entries_by_id = {}
    for i, entry in enumerate(entries):
        ind = _build_character_creation_individual(entry, season="NL", index=i)
        ind.league = "新人"
        candidates.append(ind)
        # 新人リーグの対局で実際に使われたparams・覚醒判定結果を勝者データに引き継ぐ。
        # こうしないと、本戦で実際にDリーグへ参入する際に別の乱数でparams・覚醒が
        # 再抽選されてしまい、新人リーグを勝ち上がった個体と実際に参入する個体が
        # 食い違ってしまう（タイプのみ指定・自動生成の場合は特にparamsが未指定のため）
        entries_by_id[ind.id] = {**entry, "params": dict(ind.params), "awakened_param": ind.awakened_param}

    if len(candidates) < 2:
        return [], [], []

    ranked, match_log, score, record = run_swiss_league(
        candidates, rounds=rounds, depth=depth, league_name="新人リーグ",
    )

    standings = []
    for rank, ind in enumerate(ranked, 1):
        rec = record.get(ind.id, {"win": 0, "loss": 0, "draw": 0})
        entry = entries_by_id[ind.id]
        standings.append({
            "rank": rank, "individual_id": ind.id, "display_name": ind.display_name,
            "win": rec["win"], "loss": rec["loss"], "draw": rec["draw"],
            "auto_generated": bool(entry.get("auto_generated")),
            "promoted": rank <= slots_needed,
        })

    winners = ranked[:slots_needed]
    winner_entries = [entries_by_id[w.id] for w in winners]
    return winner_entries, standings, match_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--submissions-path", default=None,
                         help="当日投稿分のJSONファイル（未指定/存在しない場合は0件として扱う）")
    args = parser.parse_args()

    state = load_season_state(args.data_dir)
    target_season = state.get("current_season", 0) + 1
    result_path = os.path.join(args.data_dir, "newcomer_league", f"for_season_{target_season}.json")

    # 本戦（run_season.py）は新人リーグの実行後に1回だけ走る想定で、
    # target_seasonはcurrent_seasonから計算するだけの値。そのため、想定より前に
    # （手動実行や、本戦側のスケジュール遅延・失敗でcurrent_seasonが進まないまま）
    # このスクリプトが同じ季に対して二重に実行されると、前回の結果ファイルを
    # 全く別の対局内容で気付かずに上書きしてしまう（勝者・順位表・対局ログが
    # ブラウザ表示と実際にDリーグへ参入する個体で食い違う原因になる）。
    # 既に同じ季の結果ファイルが存在する場合は、安全のため再実行をスキップする。
    if os.path.exists(result_path):
        print(f"::warning::第{target_season}季分の新人リーグ結果は既に存在します（{result_path}）。"
              f"本戦がまだこの季を消化していない可能性が高いため、二重実行とみなして"
              f"今回の実行はスキップします（結果の上書きを防止）", flush=True)
        return

    submissions = []
    if args.submissions_path and os.path.exists(args.submissions_path):
        with open(args.submissions_path, "r", encoding="utf-8") as f:
            submissions = json.load(f) or []
    submissions = submissions[:NEWCOMER_SUBMISSION_CAP]
    print(f"[DEBUG] 当日投稿分: {len(submissions)}件", flush=True)

    slots_needed = state.get("pending_newcomer_slots", 0)
    print(f"[DEBUG] 今夜のDリーグ新規参入枠: {slots_needed}名", flush=True)

    registry = NameRegistry()
    winner_entries, standings, match_log = run_newcomer_league(submissions, slots_needed, registry)

    winners_path = os.path.join(args.data_dir, "newcomer_winners.json")
    with open(winners_path, "w", encoding="utf-8") as f:
        json.dump(winner_entries, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] 新人リーグ勝者: {len(winner_entries)}名を{winners_path}に保存しました", flush=True)

    os.makedirs(os.path.join(args.data_dir, "newcomer_league"), exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "for_season": target_season, "slots_needed": slots_needed,
            "submission_count": len(submissions), "standings": standings, "match_log": match_log,
        }, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] 新人リーグ結果を{result_path}に保存しました", flush=True)


if __name__ == "__main__":
    main()
