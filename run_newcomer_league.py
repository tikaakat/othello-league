"""
新人リーグ（AM実行）：その日投稿されたキャラクリエイトのリクエストを集めて、
スイス方式のミニリーグ（1人あたり15局）で競わせる。上位者（前回のシーズンで
Dリーグに新規参入した人数分）だけが、この日の夜に実行される本戦でDリーグへ
新規参入する。

投稿が目標人数（30名）に満たない場合は、自動生成の候補で埋める。この自動生成候補は
毎回使い捨てにはせず、敗退した個体をnewcomer_candidate_pool.jsonへ持ち越し、次回以降の
新人リーグで同じ能力・同じ年齢のまま再挑戦できるようにする（年齢制：本戦のDリーグ
強制引退と同じ考え方で、NEWCOMER_RETIREMENT_AGE歳に達しても勝ち上がれなければ、
そこで引退してプールから外れ、新しい自動生成候補に入れ替わる。開始年齢は通常の新弟子と
同じ18〜24歳のランダムなので、挑戦できる回数には個体差がある）。
投稿（プレイヤーがキャラクリエイトしたもの）はこの持ち越しの対象外：敗退しても自動で
再挑戦はせず、投稿者が望めば改めて投稿し直す形のまま（1投稿＝1回の挑戦という前提を保つ）。
本戦（run_season.py）とは別プロセス・別スケジュール（AM実行）で動かす想定。
"""
import argparse
import json
import os
import random
import re

from othello_league.league import _build_character_creation_individual
from othello_league.swiss import run_swiss_league
from othello_league.names import NameRegistry
from othello_league.io_utils import (
    load_season_state,
    load_newcomer_candidate_pool, save_newcomer_candidate_pool,
)

NEWCOMER_LEAGUE_DEPTH = 3
NEWCOMER_LEAGUE_ROUNDS = 15
NEWCOMER_TARGET_POOL = 30
NEWCOMER_SUBMISSION_CAP = 40
# 自動生成候補が持ち越しで再挑戦できる年齢の上限（本戦のDリーグ強制引退（60歳）と
# 同じ発想。この歳に達しても勝ち上がれなければプールから外す＝引退扱い。
# 開始年齢は通常の新弟子と同じ18〜24歳のランダムなので、挑戦回数には個体差がある）
NEWCOMER_RETIREMENT_AGE = 26
CHARACTER_TYPES = ["balanced", "aggressive", "defensive", "corner"]

# main()が計算したresult_pathを一時保存する場所（git管理外）。
# push失敗時の自己修復後、relabel_if_stale()がこれを読んで対象季のズレを検出する。
RESULT_MARKER_PATH = "/tmp/newcomer_result_marker.txt"


def _build_entry_pool(submissions, retry_pool, target, registry):
    """
    今回の新人リーグに出場する候補一覧を組み立てる。優先順位は
    投稿（submissions）＞持ち越し中の自動生成候補（retry_pool、待機が長い順）＞
    新規の自動生成候補（不足分を埋める）。
    各entryには"source"（submission/retry/fresh_auto）を付与する。retryの場合は
    前回までの年齢（"initial_age"）をそのまま引き継ぎ、再抽選されないようにする
    （fresh_autoは通常の新弟子と同じく、この後の個体生成時に18〜24歳からランダムに決まる）
    """
    filled = []
    for s in submissions:
        filled.append({**s, "source": "submission"})
    for c in retry_pool:
        if len(filled) >= target:
            break
        filled.append({**c, "source": "retry", "initial_age": c.get("age")})
    while len(filled) < target:
        filled.append({
            "name": registry.generate(),
            "type": random.choice(CHARACTER_TYPES),
            "auto_generated": True,
            "source": "fresh_auto",
        })
    return filled


def run_newcomer_league(submissions, slots_needed, registry, retry_pool=None,
                         depth=NEWCOMER_LEAGUE_DEPTH,
                         rounds=NEWCOMER_LEAGUE_ROUNDS, target_pool=NEWCOMER_TARGET_POOL,
                         retirement_age=NEWCOMER_RETIREMENT_AGE):
    """
    submissions: [{"name":, "type":, "params":}, ...]（当日投稿分、上限は呼び出し側で適用済み想定）
    slots_needed: 今夜のDリーグ新規参入枠（前回シーズンの新規参入人数）
    retry_pool: 持ち越し中の自動生成候補 [{"name":,"type":,"params":,"awakened_param":,"age":}, ...]
    戻り値: (winner_entries, standings, match_log, next_retry_pool)
      winner_entries: 勝者の元の投稿データ（{"name":,"type":,"params":,"initial_age":}形式）のリスト。
        initial_ageを含めることで、本戦で実際にDリーグへ参入する際も持ち越した年齢のまま参入する
      standings: 順位表（表示用）
      match_log: 対局ログ（表示用）
      next_retry_pool: 次回の新人リーグに持ち越す自動生成候補の一覧
    """
    entries = _build_entry_pool(submissions, retry_pool or [], target_pool, registry)

    candidates = []
    entries_by_id = {}
    for i, entry in enumerate(entries):
        ind = _build_character_creation_individual(entry, season="NL", index=i)
        ind.league = "新人"
        candidates.append(ind)
        # 新人リーグの対局で実際に使われたparams・覚醒判定結果・年齢を引き継ぐ。
        # こうしないと、本戦で実際にDリーグへ参入する際（勝者）や次回の持ち越し時
        # （敗者のうちretry対象）に別の乱数でparams・覚醒・年齢が再抽選されてしまい、
        # 新人リーグで戦った個体と食い違ってしまう（タイプのみ指定・自動生成の
        # 場合は特にparamsが未指定のため）
        entries_by_id[ind.id] = {
            **entry, "params": dict(ind.params), "awakened_param": ind.awakened_param,
            "initial_age": ind.initial_age,
        }

    if len(candidates) < 2:
        return [], [], [], (retry_pool or [])

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
    winner_ids = {w.id for w in winners}
    winner_entries = [entries_by_id[w.id] for w in winners]

    # 敗退者のうち、自動生成（持ち越し中 or 今回新規）だったものだけを次回へ持ち越す。
    # 投稿（source="submission"）は対象外：敗退しても自動で再挑戦はさせない
    next_retry_pool = []
    for ind in ranked:
        if ind.id in winner_ids:
            continue
        entry = entries_by_id[ind.id]
        if entry.get("source") not in ("retry", "fresh_auto"):
            continue
        next_age = entry["initial_age"] + 1
        if next_age >= retirement_age:
            continue  # 年齢上限に達した＝引退してプールから外れる
        next_retry_pool.append({
            "name": entry.get("name"), "type": entry.get("type"),
            "params": entry["params"], "awakened_param": entry.get("awakened_param"),
            "auto_generated": True, "age": next_age,
        })

    return winner_entries, standings, match_log, next_retry_pool


def relabel_if_stale(data_dir, marker_path=RESULT_MARKER_PATH):
    """
    git pushが「本戦(evolve.yml)側のコミットが先に取り込まれていた」ことを理由に失敗し、
    git reset --mixedで最新originの上に積み直す自己修復が働いた場合を想定したチェック。

    このスクリプトの対局計算（スイスリーグ、数分〜十数分かかる）が走っている間に、
    ちょうど本戦のバッチが完了してcurrent_seasonが進んでしまうと、実行開始時点で
    読んだcurrent_seasonを元に決めたtarget_season（＝ファイル名）は、コミット時点では
    既に1つずれた値になっている。自己修復はファイルの中身・ファイル名を一切
    再計算しないため、ズレたままの季番号で結果がコミットされてしまい、
    「本戦側は次の季からその新人を迎えるのに、表示上は1つ前の季の結果として
    保存される」という食い違いが起きる。

    ここで自己修復後の最新season_state.jsonを元にtarget_seasonを再計算し、
    ズレていればファイル名・内容（for_seasonフィールド）を正しい季番号へ修正する。
    修正先の季番号が既に別の結果で埋まっている場合は、二重実行とみなし
    今回の結果を破棄する（run_newcomer_league.py本体の重複防止ガードと同じ扱い）。
    """
    if not os.path.exists(marker_path):
        return
    with open(marker_path, "r", encoding="utf-8") as f:
        old_path = f.read().strip()
    if not old_path or not os.path.exists(old_path):
        return

    m = re.search(r"for_season_(\d+)\.json$", old_path)
    if not m:
        return
    old_season = int(m.group(1))

    state = load_season_state(data_dir)
    correct_season = state.get("current_season", 0) + 1
    if correct_season == old_season:
        return

    correct_path = os.path.join(data_dir, "newcomer_league", f"for_season_{correct_season}.json")
    winners_path = os.path.join(data_dir, "newcomer_winners.json")

    if os.path.exists(correct_path):
        print(f"::warning::対象季が第{old_season}季から第{correct_season}季へずれていましたが、"
              f"第{correct_season}季の結果は既に存在するため、二重実行とみなし今回の結果は破棄します",
              flush=True)
        os.remove(old_path)
        if os.path.exists(winners_path):
            os.remove(winners_path)
        return

    print(f"::warning::本戦の進行により対象季が第{old_season}季→第{correct_season}季へずれたため、"
          f"結果ファイルを{correct_path}へリネームします", flush=True)
    with open(old_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    payload["for_season"] = correct_season
    os.remove(old_path)
    with open(correct_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--submissions-path", default=None,
                         help="当日投稿分のJSONファイル（未指定/存在しない場合は0件として扱う）")
    parser.add_argument("--relabel-check", action="store_true",
                         help="push失敗からの自己修復（git reset --mixed）の直後に呼び出し、"
                              "対象季のズレを検出・修正するモード。通常実行はしない")
    args = parser.parse_args()

    if args.relabel_check:
        relabel_if_stale(args.data_dir)
        return

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

    retry_pool = load_newcomer_candidate_pool(args.data_dir)
    print(f"[DEBUG] 持ち越し中の自動生成候補: {len(retry_pool)}件", flush=True)

    registry = NameRegistry()
    winner_entries, standings, match_log, next_retry_pool = run_newcomer_league(
        submissions, slots_needed, registry, retry_pool=retry_pool,
    )

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

    save_newcomer_candidate_pool(args.data_dir, next_retry_pool)
    print(f"[DEBUG] 次回へ持ち越す自動生成候補: {len(next_retry_pool)}件を保存しました", flush=True)

    with open(RESULT_MARKER_PATH, "w", encoding="utf-8") as f:
        f.write(result_path)


if __name__ == "__main__":
    main()
