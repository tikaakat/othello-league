"""ファンサイト用の軽量版season_state.jsonを作る（generate_news.ymlのデプロイで使う）。
元のseason_state.jsonは数十MBあり全ページの読み込みが極端に遅くなるため、ファンサイトが使う部分
（現在の季・タイトル保持者）と、奨励会・三段リーグの名簿だけを抜き出す"""
import json
import os
import sys

KEYS = ("current_season", "titleholders")


def _shoreikai_summary(data_dir, state):
    """奨励会・三段リーグの名簿と、直近の新人リーグ（三段リーグ）での入会・昇段・退会（ファンサイトの奨励会ページ用）"""
    path = os.path.join(data_dir, "shoreikai.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        members = (json.load(f) or {}).get("members", [])
    names = {r.get("id"): r.get("display_name") for r in state.get("retired_archive", [])}
    rp = os.path.join(data_dir, "rosters.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            for lst in (json.load(f) or {}).values():
                names.update({x.get("id"): x.get("display_name") for x in lst})
    sandan = []
    cp = os.path.join(data_dir, "newcomer_candidate_pool.json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            sandan = json.load(f) or []
    row = lambda e: {"name": e.get("name"), "age": e.get("age"), "master_name": names.get(e.get("parent_a_id")),
                     "joined_season": e.get("joined_season")}
    log, log_season = None, None
    nl_dir = os.path.join(data_dir, "newcomer_league")
    if os.path.isdir(nl_dir):
        seasons = sorted(int(n[len("for_season_"):-5]) for n in os.listdir(nl_dir)
                         if n.startswith("for_season_") and n.endswith(".json"))
        for s in reversed(seasons):
            with open(os.path.join(nl_dir, f"for_season_{s}.json"), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("shoreikai"):
                log, log_season = d["shoreikai"], s
                break
    return {"members": [row(e) for e in members], "sandan": [row(e) for e in sandan],
            "log": log, "log_season": log_season}


def main(data_dir, out_path):
    with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    out = {k: state.get(k) for k in KEYS}
    out["shoreikai"] = _shoreikai_summary(data_dir, state)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data", sys.argv[2] if len(sys.argv) > 2 else "/tmp/news_out/season_state.json")
