"""開幕メンバー（第1季の選手。IDがCCで始まらない）に、開幕前からの師弟関係を後から付ける一回限りの処理。
全員が祖だと系統が数十に分かれていたため、最年長の8人を大師匠（祖）にし、残りをその弟子・孫弟子にする
（othello_league.league.assign_initial_masters。今後のリセットでは最初のメンバーを作るときに同じ処理をする）。
開幕後に生まれた弟子の師匠はそのまま。clan_root_id・generationは全員（現役・引退・奨励会・三段リーグ）を付け直す。

使い方: python3 scripts/regroup_initial_lineage.py [data_dir]
"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from othello_league.league import assign_initial_masters, assign_generations, assign_lineage_roots  # noqa: E402


def main(data_dir="data"):
    st_path = os.path.join(data_dir, "season_state.json")
    ro_path = os.path.join(data_dir, "rosters.json")
    sk_path = os.path.join(data_dir, "shoreikai.json")
    sd_path = os.path.join(data_dir, "newcomer_candidate_pool.json")
    state = json.load(open(st_path, encoding="utf-8"))
    rosters = json.load(open(ro_path, encoding="utf-8"))
    active = [x for v in rosters.values() for x in v]
    retired = state.get("retired_archive", [])
    everyone = active + retired
    initial = [x for x in everyone if not str(x["id"]).startswith("CC")]

    # 大師匠向きの強さ：最高Elo＋タイトル獲得期数
    held = collections.Counter()
    for e in state.get("title_history", []):
        if e.get("event") != "初代襲名" and e.get("holder_id"):
            held[e["holder_id"]] += 1
    score = lambda x: (x.get("peak_elo") or x.get("elo") or 1500) + 20 * held[x["id"]]
    roots = assign_initial_masters(initial, score, rng=random.Random(20261108))
    assign_generations(everyone)
    assign_lineage_roots(everyone, [])

    # 奨励会・三段リーグの子：師匠の系統と代を引き継ぐ
    by_id = {x["id"]: x for x in everyone}
    for path in (sk_path, sd_path):
        if not os.path.exists(path):
            continue
        d = json.load(open(path, encoding="utf-8"))
        lst = d["members"] if isinstance(d, dict) else d
        for e in lst:
            m = by_id.get(e.get("parent_a_id"))
            if m:
                e["clan_root_id"] = m["clan_root_id"]
                e["generation"] = m.get("generation", 0) + 1
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1 if isinstance(d, dict) else 2)

    json.dump(rosters, open(ro_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(state, open(st_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    names = {x["id"]: x["display_name"] for x in everyone}
    print("大師匠:", "、".join(f"{names[r]}（{by_id[r].get('initial_age')}歳）" for r in roots))
    c = collections.Counter(x["clan_root_id"] for x in everyone)
    print("系統（現役・引退の人数）:", "、".join(f"{names.get(k, k)}系 {v}名" for k, v in c.most_common()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
