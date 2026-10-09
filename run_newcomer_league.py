"""
新人リーグ（AM実行）：その日投稿されたキャラクリエイトのリクエストを集めて、
スイス方式のミニリーグ（1人あたり15局）で競わせる。上位者（前回のシーズンで
Dリーグに新規参入した人数分）だけが、この日の夜に実行される本戦でDリーグへ
新規参入する。

投稿が目標人数（30名）に満たない場合は、自動生成の候補で埋める。
非昇格者のうち上位5名（投稿・自動生成を問わない）だけを次回の新人リーグに持ち越す
（newcomer_candidate_pool.json）。年齢制限は設けない：持ち越し中も年齢は重ねるが、
何歳でも上位5名に入り続ける限り再挑戦できる。

新人リーグのロスターを組む時点（＝本戦でDリーグに参入するよりも前）で、師匠（弟子で
あれば誰の弟子か）を決めておく。これにより、新人リーグを戦っている時点で既に
師弟関係が決まった状態になる（本戦でのDリーグ参入時に初めて師匠を決めるのではない）。
持ち越し中の候補は、最初に決まった師弟関係をそのまま引き継ぐ（再抽選しない）。
本戦（run_season.py）とは別プロセス・別スケジュール（AM実行）で動かす想定。
"""
import argparse
import json
import os
import random
import re

from othello_league.league import (
    _build_character_creation_individual, _pick_master, MASTER_MIN_AGE,
    count_existing_disciples,
)
from othello_league.swiss import run_swiss_league
from othello_league.play import play_decisive_match
from othello_league.names import NameRegistry
from othello_league.io_utils import (
    load_season_state, load_rosters,
    load_newcomer_candidate_pool, save_newcomer_candidate_pool,
)

NEWCOMER_LEAGUE_DEPTH = 3
NEWCOMER_LEAGUE_ROUNDS = 15
NEWCOMER_TARGET_POOL = 30
NEWCOMER_SUBMISSION_CAP = 40
NEWCOMER_RETRY_KEEP_TOP_N = 5  # 非昇格者のうち、次回へ持ち越すのは上位何名まで
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
        name, gender = registry.generate()
        filled.append({
            "name": name,
            "gender": gender,
            "type": random.choice(CHARACTER_TYPES),
            "auto_generated": True,
            "source": "fresh_auto",
        })
    return filled


def _run_playoff_round(tied_ids, by_id, depth):
    """
    tied_idsの総当たり（1回戦ずつ、引き分け無しで決着）を1ラウンド行い、
    (勝数dict, 対局ログ)を返す"""
    wins = {iid: 0 for iid in tied_ids}
    matches = []
    for i in range(len(tied_ids)):
        for j in range(i + 1, len(tied_ids)):
            a_id, b_id = tied_ids[i], tied_ids[j]
            outcome_a, games = play_decisive_match(by_id[a_id], by_id[b_id], depth=depth)
            winner_id = a_id if outcome_a == "win" else b_id
            wins[winner_id] += 1
            matches.append({
                "a": a_id, "b": b_id, "a_name": by_id[a_id].display_name,
                "b_name": by_id[b_id].display_name, "winner": winner_id, "games": games,
            })
    return wins, matches


def resolve_promotion_playoff(tied_ids, by_id, remaining_slots, depth, _round=1, _max_rounds=5):
    """
    昇格枠の境界で同成績になった候補者（tied_ids）の中からremaining_slots名を、
    総当たり・引き分け無しのプレーオフで決める（新人リーグの順位はEloで同点が
    決まらず、全員が新規参入者で初期Eloも揃っているため、実力に基づかない
    投稿順タイブレークになってしまっていた不備への対応）。

    勝数で順位付けし、それでも境界で同数が残れば、その対象だけを絞り込んで
    再度プレーオフを行う（収束するまで、最大_max_rounds回）。万一収束しなかった
    場合のみ、現在のElo順（対局によりこの時点では差がついている）で確定する。

    戻り値: (昇格者idリスト, 非昇格者idリスト, プレーオフラウンドのログ)
    """
    if len(tied_ids) <= remaining_slots:
        return list(tied_ids), [], []
    if len(tied_ids) < 2 or _round > _max_rounds:
        ordered = sorted(tied_ids, key=lambda iid: -by_id[iid].elo)
        return ordered[:remaining_slots], ordered[remaining_slots:], []

    wins, matches = _run_playoff_round(tied_ids, by_id, depth)
    ordered = sorted(tied_ids, key=lambda iid: -wins[iid])
    boundary_wins = wins[ordered[remaining_slots - 1]]
    above = [iid for iid in ordered if wins[iid] > boundary_wins]
    at_boundary = [iid for iid in ordered if wins[iid] == boundary_wins]
    below = [iid for iid in ordered if wins[iid] < boundary_wins]

    rounds_log = [{"round": _round, "matches": matches}]
    slots_left = remaining_slots - len(above)
    sub_promoted, sub_non_promoted, sub_log = resolve_promotion_playoff(
        at_boundary, by_id, slots_left, depth, _round=_round + 1, _max_rounds=_max_rounds,
    )
    return above + sub_promoted, sub_non_promoted + below, rounds_log + sub_log


def _determine_promotion(ranked, score, slots_needed, by_id, depth):
    """
    スイス方式終了後の最終スコアから昇格者を確定する。昇格枠の境界に同成績が
    並ばない（通常のケース）場合はそのままrank<=slots_needed、並ぶ場合は
    resolve_promotion_playoff()でプレーオフを行う。
    戻り値: (昇格者idの集合, 表示用順位（playoffで並び替え済み）のindividual_idリスト,
             プレーオフログ)
    """
    if slots_needed <= 0 or slots_needed >= len(ranked):
        promoted_ids = {ind.id for ind in ranked[:slots_needed]}
        return promoted_ids, [ind.id for ind in ranked], []

    boundary_score = score[ranked[slots_needed - 1].id]
    clearly_above = [ind.id for ind in ranked if score[ind.id] > boundary_score]
    tied_ids = [ind.id for ind in ranked if score[ind.id] == boundary_score]
    remaining_slots = slots_needed - len(clearly_above)

    promoted_tied, non_promoted_tied, playoff_log = resolve_promotion_playoff(
        tied_ids, by_id, remaining_slots, depth,
    )
    promoted_ids = set(clearly_above) | set(promoted_tied)

    # 表示順も、境界で並んでいた同成績グループだけプレーオフの結果順に並べ替える
    # （順位欄とpromotedフラグが食い違わないようにするため）。グループ外の順序は変えない
    tied_set = set(tied_ids)
    reordered_tied = promoted_tied + non_promoted_tied
    display_order = []
    inserted = False
    for ind in ranked:
        if ind.id in tied_set:
            if not inserted:
                display_order.extend(reordered_tied)
                inserted = True
            continue
        display_order.append(ind.id)

    return promoted_ids, display_order, playoff_log


def run_newcomer_league(submissions, slots_needed, registry, retry_pool=None,
                         depth=NEWCOMER_LEAGUE_DEPTH,
                         rounds=NEWCOMER_LEAGUE_ROUNDS, target_pool=NEWCOMER_TARGET_POOL,
                         pool=None, titleholder_ids=frozenset()):
    """
    submissions: [{"name":, "type":, "params":}, ...]（当日投稿分、上限は呼び出し側で適用済み想定）
    slots_needed: 今夜のDリーグ新規参入枠（前回シーズンの新規参入人数）
    retry_pool: 持ち越し中の候補（前回上位5名）
      [{"name":,"type":,"params":,"awakened_param":,"age":,"parent_a_id":,"clan_root_id":,
        "generation":,"volatility":,"auto_generated":}, ...]
    pool: 師匠選出に使う現役個体一覧（A〜D全リーグ）。Noneの場合、新規の師弟関係は
      決めない（全員が無流派の新規開祖扱いになる。テスト・後方互換用）
    titleholder_ids: 師匠選出の重み付けに使うタイトル保持者ID集合
    戻り値: (winner_entries, standings, match_log, next_retry_pool, playoff_log)
      winner_entries: 勝者の元の投稿データ（{"name":,"type":,"params":,"initial_age":,
        "parent_a_id":,"clan_root_id":,"generation":,"volatility":}形式）のリスト。
        これらを含めることで、本戦で実際にDリーグへ参入する際も新人リーグ時点で
        決まった値のまま参入する（師弟関係も年齢も再抽選しない）
      standings: 順位表（表示用。昇格枠の境界で同成績が並んだ場合は、プレーオフの
        結果順に並べ替えてある）
      match_log: 対局ログ（表示用）
      next_retry_pool: 次回の新人リーグに持ち越す候補（非昇格者の上位5名）
      playoff_log: 昇格枠の境界で同成績が並んだ場合のプレーオフ対局ログ
        （[{"round":, "matches":[{"a":,"b":,"a_name":,"b_name":,"winner":,"games":}, ...]}, ...]）。
        同成績が発生しなかった場合は空リスト。永続保存はせず、この季の結果ファイルにのみ記録する
    """
    entries = _build_entry_pool(submissions, retry_pool or [], target_pool, registry)

    # 師匠候補（30歳以上の現役個体。該当者がいなければ制限なしにフォールバック）。
    # 一門分岐の判定（弟子5名超で分岐）に使うdisciple_countsは、このバッチ全体で
    # 使い回し、割り当てるたびに加算する（count_existing_disciplesで現在の人数から開始）
    active_pool = [ind for ind in (pool or []) if not ind.retired]
    eligible_masters = [ind for ind in active_pool if ind.age >= MASTER_MIN_AGE] or active_pool
    disciple_counts = count_existing_disciples(active_pool)

    candidates = []
    entries_by_id = {}
    for i, entry in enumerate(entries):
        # retry（持ち越し）は前回決定した師弟関係をentry自体が既に持っている
        # （"parent_a_id"キーの有無で判定）ので、ここでは再抽選しない。
        # submission・fresh_autoは、新人リーグのロスターを組むこの時点で師匠を決める
        # （本戦でのDリーグ参入時まで待たない）
        if "parent_a_id" in entry:
            master = None
        else:
            master = _pick_master(eligible_masters, titleholder_ids, allow_new_founder=False) if eligible_masters else None

        ind = _build_character_creation_individual(
            entry, season="NL", index=i, master=master, disciple_counts=disciple_counts,
        )
        ind.league = "新人"
        candidates.append(ind)
        # 新人リーグの対局で実際に使われたparams・師弟関係・覚醒判定結果・年齢を引き継ぐ。
        # こうしないと、本戦で実際にDリーグへ参入する際（勝者）や次回の持ち越し時
        # （非昇格者の上位5名）に別の乱数で再抽選されてしまい、新人リーグで戦った個体と
        # 食い違ってしまう
        entries_by_id[ind.id] = {
            **entry, "params": dict(ind.params), "awakened_param": ind.awakened_param,
            "initial_age": ind.initial_age, "parent_a_id": ind.parent_a_id,
            "clan_root_id": ind.clan_root_id, "generation": ind.generation,
            "volatility": ind.volatility,
        }

    if len(candidates) < 2:
        return [], [], [], (retry_pool or []), []

    ranked, match_log, score, record = run_swiss_league(
        candidates, rounds=rounds, depth=depth, league_name="新人リーグ",
    )

    # スイス方式終了時点のスコアだけでは、昇格枠の境界に複数名が同成績で並んだ場合の
    # 決着がつかない（新人リーグは全員が新規参入者で初期Eloも揃っているため、
    # 従来のEloタイブレークは実質無意味で、投稿順という実力と無関係な決定になっていた）。
    # 境界で同成績が並んだ場合のみ、該当者同士のプレーオフ（引き分け無し）で昇格者を決める
    by_id = {ind.id: ind for ind in ranked}
    promoted_ids, display_order, playoff_log = _determine_promotion(
        ranked, score, slots_needed, by_id, depth,
    )

    # 師匠の表示名（新人リーグ結果タブで「師匠：〜」を表示するため）。
    # 師匠は既存個体（pool）の中から選ばれているので、名前解決にはpoolを使う
    master_name_by_id = {ind.id: ind.display_name for ind in (pool or [])}

    standings = []
    for rank, iid in enumerate(display_order, 1):
        ind = by_id[iid]
        rec = record.get(iid, {"win": 0, "loss": 0, "draw": 0})
        entry = entries_by_id[iid]
        standings.append({
            "rank": rank, "individual_id": iid, "display_name": ind.display_name,
            "win": rec["win"], "loss": rec["loss"], "draw": rec["draw"],
            "auto_generated": bool(entry.get("auto_generated")),
            "promoted": iid in promoted_ids,
            "master_id": ind.parent_a_id,
            "master_name": master_name_by_id.get(ind.parent_a_id) if ind.parent_a_id else None,
        })

    winners = [ind for ind in ranked if ind.id in promoted_ids]
    winner_entries = [entries_by_id[w.id] for w in winners]

    # 非昇格者のうち、成績上位5名（投稿・自動生成を問わない）だけを次回へ持ち越す。
    # display_orderは既にプレーオフ結果を反映した最終順位なので、winner以降の先頭5名がそのまま対象
    non_promoted = [by_id[iid] for iid in display_order if iid not in promoted_ids]
    next_retry_pool = []
    for ind in non_promoted[:NEWCOMER_RETRY_KEEP_TOP_N]:
        entry = entries_by_id[ind.id]
        next_retry_pool.append({
            "name": entry.get("name"), "type": entry.get("type"),
            "params": entry["params"], "awakened_param": entry.get("awakened_param"),
            "auto_generated": bool(entry.get("auto_generated")), "age": entry["initial_age"] + 1,
            "parent_a_id": entry.get("parent_a_id"), "clan_root_id": entry.get("clan_root_id"),
            "generation": entry.get("generation", 0), "volatility": entry.get("volatility"),
        })

    return winner_entries, standings, match_log, next_retry_pool, playoff_log


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
    print(f"[DEBUG] 持ち越し中の候補（前回上位{NEWCOMER_RETRY_KEEP_TOP_N}名）: {len(retry_pool)}件", flush=True)

    # 師匠選出用に現役ロスターとタイトル保持者IDを読み込む（新人リーグのロスターを
    # 組む時点で師弟関係を決めるため、本戦のDリーグ補充と同じ情報が要る）
    rosters = load_rosters(args.data_dir) or {"A": [], "B": [], "C": [], "D": []}
    pool = [ind for league_list in rosters.values() for ind in league_list]
    titleholder_ids = {
        info["id"] for info in (state.get("titleholders") or {}).values() if info and info.get("id")
    }

    registry = NameRegistry()
    winner_entries, standings, match_log, next_retry_pool, playoff_log = run_newcomer_league(
        submissions, slots_needed, registry, retry_pool=retry_pool,
        pool=pool, titleholder_ids=titleholder_ids,
    )
    if playoff_log:
        print(f"[DEBUG] 昇格枠の境界で同成績が発生したため、プレーオフを{len(playoff_log)}ラウンド実施しました", flush=True)

    winners_path = os.path.join(args.data_dir, "newcomer_winners.json")
    with open(winners_path, "w", encoding="utf-8") as f:
        json.dump(winner_entries, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] 新人リーグ勝者: {len(winner_entries)}名を{winners_path}に保存しました", flush=True)

    os.makedirs(os.path.join(args.data_dir, "newcomer_league"), exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "for_season": target_season, "slots_needed": slots_needed,
            "submission_count": len(submissions), "standings": standings, "match_log": match_log,
            "promotion_playoff": playoff_log,
        }, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] 新人リーグ結果を{result_path}に保存しました", flush=True)

    save_newcomer_candidate_pool(args.data_dir, next_retry_pool)
    print(f"[DEBUG] 次回へ持ち越す自動生成候補: {len(next_retry_pool)}件を保存しました", flush=True)

    with open(RESULT_MARKER_PATH, "w", encoding="utf-8") as f:
        f.write(result_path)


if __name__ == "__main__":
    main()
