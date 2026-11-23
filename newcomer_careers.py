"""データサイトの「歴代のデビュー」用：三段リーグからDリーグにデビューした全員の、その後の成績一覧を作る
（generate_news.ymlのデプロイでファンサイトの置き場所に newcomer_careers.json として置き、データサイトが読む）。

1人1行で：奨励会（入会年齢・期数）、三段リーグ（抜けた期・その期の成績と順位・全期の合計）、
Dでの1年目、C・B・Aに初めて上がった季、A在籍季数、タイトル保持期数、最高Elo、通算（全対局）、今の所属または引退。
通算はデータサイトの選手ページと同じ数え方（対局単位の勝-敗-分。リーグ戦・タイトル戦・予選すべて）

使い方: python3 newcomer_careers.py data 出力先.json
"""
import collections
import json
import os
import sys

LEAGUE_ORDER = "ABCDF"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _seasons(dirpath, prefix):
    out = []
    for name in os.listdir(dirpath):
        if name.startswith(prefix) and name.endswith(".json"):
            try:
                out.append(int(name[len(prefix):-5]))
            except ValueError:
                pass
    return sorted(out)


def build(data_dir):
    state = _load(os.path.join(data_dir, "season_state.json"))
    seasons = _seasons(os.path.join(data_dir, "standings"), "season_")
    if not seasons:
        return {"last_season": 0, "rows": []}
    last = seasons[-1]
    standings = {s: _load(os.path.join(data_dir, "standings", f"season_{s}.json")) for s in seasons}

    # 選手の能力・引退情報（現役＋引退）
    people = {}
    rp = os.path.join(data_dir, "rosters.json")
    if os.path.exists(rp):
        for lst in _load(rp).values():
            for p in lst:
                people[p["id"]] = p
    for p in state.get("retired_archive", []):
        people.setdefault(p["id"], p)

    # 季ごとのA〜D・フリークラスの所属と成績
    row_of = {}
    debut = {}
    for s in seasons:
        for r in standings[s]:
            if len(r.get("league") or "") != 1:
                continue
            row_of[(s, r["individual_id"])] = r
            if s >= 2 and "new" in (r.get("movement") or ""):
                debut.setdefault(r["individual_id"], s)

    # タイトルを保持していた期数（季の終わりの保持者を数える）
    holder = {}
    held = collections.defaultdict(collections.Counter)
    by_season = collections.defaultdict(list)
    for e in state.get("title_history", []):
        by_season[e.get("season")].append(e)
    for s in range(1, last + 1):
        for e in by_season.get(s, []):
            if e.get("holder_id"):
                holder[e["title"]] = e["holder_id"]
        for t, h in holder.items():
            held[h][t] += 1

    # 通算（全対局。対局単位）
    record = collections.defaultdict(lambda: [0, 0, 0])
    mdir = os.path.join(data_dir, "matches")
    for s in _seasons(mdir, "season_"):
        for m in _load(os.path.join(mdir, f"season_{s}.json")):
            i = {"win": 0, "loss": 1, "draw": 2}.get(m.get("result"))
            if i is None:
                continue
            record[m["individual_a_id"]][i] += 1
            if m.get("individual_b_id"):
                record[m["individual_b_id"]][(1, 0, 2)[i]] += 1

    # 三段リーグ（新人リーグ）と奨励会の記録。名前で引くため、同名の別人に当たらないよう
    # 「デビューした季に昇格し、そこから途切れずにさかのぼれる期」だけを数える
    sandan = collections.defaultdict(list)
    joined, to_sandan = {}, {}
    ndir = os.path.join(data_dir, "newcomer_league")
    if os.path.isdir(ndir):
        for fs in _seasons(ndir, "for_season_"):
            x = _load(os.path.join(ndir, f"for_season_{fs}.json"))
            n = len(x.get("standings", []))
            for r in x.get("standings", []):
                sandan[r["display_name"]].append(
                    (fs, r.get("rank"), n, r.get("win", 0), r.get("loss", 0), bool(r.get("promoted")), r.get("sandan_terms")))
            sk = x.get("shoreikai") or {}
            for j in sk.get("joined", []):
                joined[j["name"]] = (fs, j.get("age"))
            for t in sk.get("to_sandan", []):
                to_sandan[t["name"]] = (fs, t.get("terms"), t.get("age"))

    rows = []
    for pid, s0 in sorted(debut.items(), key=lambda kv: (kv[1], kv[0])):
        first = row_of[(s0, pid)]
        name = first["display_name"]
        p = people.get(pid, {})
        seq = [(s, row_of[(s, pid)]["league"]) for s in range(s0, last + 1) if (s, pid) in row_of]
        best = min((lg for _, lg in seq), key=LEAGUE_ORDER.index)
        reach = {}
        for s, lg in seq:
            for x in "CBA":
                if LEAGUE_ORDER.index(lg) <= LEAGUE_ORDER.index(x) and x not in reach:
                    reach[x] = s
        d_size = sum(1 for r in standings[s0] if r.get("league") == "D")
        rec = record[pid]
        row = {
            "id": pid, "name": name, "season": s0, "age": p.get("initial_age"),
            "first": [first.get("win", 0), first.get("loss", 0), first.get("draw", 0), first.get("rank"), d_size],
            "best": best, "reach": [reach.get("C"), reach.get("B"), reach.get("A")],
            "seasons_a": sum(1 for _, lg in seq if lg == "A"),
            "titles": dict(held[pid]), "peak": round(p.get("peak_elo") or 0),
            "record": rec,
        }
        if p.get("retired"):
            reason = p.get("retirement_reason") or ""
            row["retired"] = ["年齢" if "60歳" in reason else "フリー10季" if "フリー" in reason else reason[:8], seq[-1][0]]
        else:
            row["now"] = seq[-1][1] if seq[-1][0] == last else None
        chain = [e for e in sandan.get(name, []) if e[0] <= s0]
        if chain and chain[-1][0] == s0 and chain[-1][5]:
            run = [chain[-1]]
            for e in reversed(chain[:-1]):
                if e[0] == run[-1][0] - 1:
                    run.append(e)
                else:
                    break
            out = run[0]
            row["sandan"] = [out[6] or len(run), out[3], out[4], out[1], out[2],
                             sum(e[3] for e in run), sum(e[4] for e in run)]
        ts, jn = to_sandan.get(name), joined.get(name)
        if ts and ts[0] <= s0:
            entry_age = jn[1] if jn and jn[0] <= s0 and jn[1] is not None else (
                ts[2] - ts[1] if ts[2] is not None and ts[1] is not None else None)
            row["shoreikai"] = [entry_age, ts[1]]
        rows.append(row)
    return {"last_season": last, "rows": rows}


if __name__ == "__main__":
    data_dir, out = sys.argv[1], sys.argv[2]
    result = build(data_dir)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"newcomer_careers: {len(result['rows'])}名（第{result['last_season']}季まで）→ {out}")
