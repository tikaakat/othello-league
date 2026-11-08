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
                     "joined_season": e.get("joined_season"), "named_by": e.get("named_by"),
                     "sandan_season": e.get("sandan_season")}
    log, log_season = None, None
    nl_dir = os.path.join(data_dir, "newcomer_league")
    if os.path.isdir(nl_dir):
        seasons = sorted(int(n[len("for_season_"):-5]) for n in os.listdir(nl_dir)
                         if n.startswith("for_season_") and n.endswith(".json"))
        for s in reversed(seasons):
            with open(os.path.join(nl_dir, f"for_season_{s}.json"), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("shoreikai"):
                # for_season_{s} は第s季から出走する分＝第s−1季の本戦のあとに開いた奨励会・三段リーグ
                log, log_season = d["shoreikai"], s - 1
                break
    return {"members": [row(e) for e in members], "sandan": [row(e) for e in sandan],
            "log": log, "log_season": log_season}


def _season_summary(data_dir, state):
    """ファンサイトのトップに出す「今季のまとめ」：直近の季のタイトル戦・Aリーグ上位・昇級者・表彰・三段リーグからのデビュー"""
    season = state.get("current_season") or 0
    if not season:
        return None
    titles = []
    for e in state.get("title_history", []):
        if e.get("season") != season:
            continue
        if e.get("event") == "初代襲名":
            titles.append({"title": e["title"], "holder": e.get("holder_name") or e.get("new_holder"), "result": "初代"})
            continue
        took = e.get("challenger_id") and e.get("challenger_id") == e.get("holder_id")
        titles.append({"title": e["title"], "holder": e.get("holder_name"), "challenger": e.get("challenger_name"),
                       "result": "奪取" if took else "防衛",
                       "score": f"{e.get('challenger_wins', 0)}-{e.get('titleholder_wins', 0)}"})
    order = ["青龍", "麒麟", "朱雀", "白虎", "玄武"]  # 序列順（青龍が1位）
    titles.sort(key=lambda t: order.index(t["title"]) if t["title"] in order else 9)
    a_top, promoted, to_free = [], {}, 0
    sp = os.path.join(data_dir, "standings", f"season_{season}.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            rows = json.load(f)
        a_top = [r["display_name"] for r in sorted((r for r in rows if r.get("league") == "A" and r.get("rank")),
                                                     key=lambda r: r["rank"])[:3]]
        for r in rows:
            mv = r.get("movement") or ""
            if "promoted" in mv and r.get("league") in ("B", "C", "D", "F"):
                promoted.setdefault(r["league"], []).append(r["display_name"])
            if r.get("league") == "D" and "relegated" in mv:
                to_free += 1
    awards = {}
    ap = os.path.join(data_dir, "news", "awards.json")
    if os.path.exists(ap):
        with open(ap, encoding="utf-8") as f:
            a = (json.load(f) or {}).get("seasons", {}).get(str(season)) or {}
        awards = {k: (a.get(k) or {}).get("name") for k in ("mvp", "rookie") if a.get(k)}
    debuts = []
    np_ = os.path.join(data_dir, "newcomer_league", f"for_season_{season + 1}.json")
    if os.path.exists(np_):
        with open(np_, encoding="utf-8") as f:
            nl = json.load(f)
        debuts = [{"name": r["display_name"], "age": r.get("age")} for r in nl.get("standings", []) if r.get("promoted")]
    return {"season": season, "titles": titles, "a_top": a_top, "promoted": promoted, "to_free": to_free,
            "awards": awards, "debuts": debuts}


def main(data_dir, out_path):
    with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    out = {k: state.get(k) for k in KEYS}
    out["shoreikai"] = _shoreikai_summary(data_dir, state)
    out["season_summary"] = _season_summary(data_dir, state)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data", sys.argv[2] if len(sys.argv) > 2 else "/tmp/news_out/season_state.json")
