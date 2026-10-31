"""ファンサイト用の軽量版season_state.jsonを作る（generate_news.ymlのデプロイで使う）。
元のseason_state.jsonは数十MBあり全ページの読み込みが極端に遅くなるため、ファンサイトが使う部分だけを抜き出し、
流（一門）の呼び名（開祖の苗字。othello_league/clan_names.py）を添える"""
import json
import os
import sys

from othello_league.clan_names import clan_names_from_data

KEYS = ("current_season", "titleholders", "clan_heads", "clan_succession_history", "clan_independence_history")


def main(data_dir, out_path):
    with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    with open(os.path.join(data_dir, "rosters.json"), encoding="utf-8") as f:
        rosters = json.load(f)
    first = {}
    s = 1
    while os.path.exists(os.path.join(data_dir, "standings", f"season_{s}.json")):
        with open(os.path.join(data_dir, "standings", f"season_{s}.json"), encoding="utf-8") as f:
            for r in json.load(f):
                first.setdefault(r["individual_id"], s)
        s += 1
    out = {k: state.get(k) for k in KEYS}
    out["clan_names"] = clan_names_from_data(rosters, state, first)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data", sys.argv[2] if len(sys.argv) > 2 else "/tmp/news_out/season_state.json")
