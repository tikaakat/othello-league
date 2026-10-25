"""
コラムのテーマ選び（generate_news.pyから使う）。

以前はコラムが毎季「連勝」「波乱の一局」の2本で固定されており、見出しも似た型が続いていた。
そこで、季ごとにいくつかのテーマの「ネタ」をデータから探し、話題の大きさ（score）を付けて、
直近の季に使ったテーマを避けながら上位2本を選ぶ。書き方の型（観戦記風・数字で読む等）も持ち回りにする。

各ネタは次の形のdictで返す：
  topic   テーマ名（直近で使ったかの判定に使う）
  score   話題の大きさ（大きいほど優先）
  lines   記事の材料にする事実（箇条書き。これ以外の出来事は書かせない）
  subject 何を主題にした記事か
  tags    記事のタグ
  related_individual_ids / related_individual_names
"""
import json
import os
from collections import Counter, defaultdict

LEAGUES = ("A", "B", "C", "D")
TITLE_NAMES = ["青龍", "朱雀", "白虎", "玄武"]

# 直近この季数に使ったテーマは避ける（話題がよほど大きければ選ばれうるよう、点数を下げるだけにする）
RECENT_TOPIC_SEASONS = 3
RECENT_TOPIC_PENALTY = 40

# 書き方の型（持ち回り）
FORMATS = [
    ("所感", "記者個人の所感を中心にした短いコラム"),
    ("観戦記風", "対局や季の流れを順に追う観戦記風の読み物"),
    ("数字で読む", "事実データの数字を軸に読み解く構成"),
    ("振り返り", "その選手（または出来事）のこれまでを振り返りながら今季を位置づける構成"),
    ("問いかけ", "読者への問いかけから入り、事実で答えていく構成"),
]


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _standings(data_dir, season):
    return _load(os.path.join(data_dir, "standings", f"season_{season}.json"), [])


def _matches(data_dir, season):
    return _load(os.path.join(data_dir, "matches", f"season_{season}.json"), [])


def _record(w, l, d=0):
    return f"{w}勝{l}敗" + (f"{d}分" if d else "")


def _main_rows(rows):
    return [r for r in rows if r.get("league") in LEAGUES]


def _title_counts(state, season):
    """season季までのタイトル獲得数（id → {タイトル: 期数}）"""
    counts = defaultdict(Counter)
    for e in state.get("title_history", []):
        if e.get("season", 0) > season:
            continue
        # holder_id はその季の番勝負の後の保持者（奪取なら挑戦者、防衛なら在位者）
        holder = e.get("holder_id") or (e.get("challenger_id") if e.get("won") else e.get("defender_id"))
        if holder:
            counts[holder][e["title"]] += 1
    return counts


# ------------------------------------------------------------------ 各テーマのネタ探し

def topic_rivalry(ctx):
    """因縁の対決：今季対戦した組のうち、通算で一方が大きく勝ち越している組（天敵）。
    今季も天敵に敗れた、または今季ついに勝った（雪辱）を取り上げる"""
    season, names = ctx["season"], ctx["names"]
    this = []
    for m in ctx["matches"][season]:
        a, b = m.get("individual_a_id"), m.get("individual_b_id")
        if a and b and m.get("result") in ("win", "loss"):
            this.append((a, b, m["result"], m.get("league")))
    if not this:
        return None
    h2h = defaultdict(lambda: [0, 0])  # (x, y) → [xの勝ち, yの勝ち]（今季より前）
    for s in range(1, season):
        for m in ctx["matches"].get(s, []):
            a, b, r = m.get("individual_a_id"), m.get("individual_b_id"), m.get("result")
            if not (a and b) or r not in ("win", "loss"):
                continue
            key = (a, b) if a < b else (b, a)
            win_a = (r == "win")
            if key[0] == a:
                h2h[key][0 if win_a else 1] += 1
            else:
                h2h[key][1 if win_a else 0] += 1
    best = None
    for a, b, r, league in this:
        key = (a, b) if a < b else (b, a)
        x, y = h2h.get(key, [0, 0])
        if x + y < 6:
            continue
        strong, weak = (key[0], key[1]) if x >= y else (key[1], key[0])
        sw, ww = max(x, y), min(x, y)
        if sw / (sw + ww) < 0.7:
            continue
        winner = a if r == "win" else b
        revenge = winner == weak
        score = 35 + (sw - ww) * 3 + (25 if revenge else 0)
        if best is None or score > best[0]:
            best = (score, strong, weak, sw, ww, revenge, league)
    if best is None:
        return None
    score, strong, weak, sw, ww, revenge, league = best
    ns, nw = names.get(strong, strong), names.get(weak, weak)
    lines = [f"{nw}にとって{ns}は、今季より前の通算対戦成績で{_record(ww, sw)}と大きく負け越している相手（天敵）だった。"]
    if revenge:
        lines.append(f"今季、{nw}は{ns}との対局（{ctx['stage_label'](league)}）に勝った。")
        subject = f"{nw}が天敵の{ns}に勝った一局（雪辱）"
    else:
        lines.append(f"今季も、{nw}は{ns}との対局（{ctx['stage_label'](league)}）に敗れた。")
        subject = f"{nw}と天敵{ns}の因縁"
    return dict(topic="因縁", score=score, lines=lines, subject=subject, tags=["コラム", "因縁の対決"],
                related_individual_ids=[weak, strong], related_individual_names=[nw, ns])


def topic_retirement(ctx):
    """引退・世代交代：今季で引退した選手のうち、タイトル経験者や上位リーグの実力者"""
    season = ctx["season"]
    rows = _main_rows(ctx["standings"][season])
    retired = [r for r in rows if r.get("movement") == "retired"]
    if not retired:
        return None
    titles = ctx["title_counts"]
    def weight(r):
        return sum(titles.get(r["individual_id"], {}).values()) * 10 + {"A": 30, "B": 15, "C": 5, "D": 0}[r["league"]]
    r = max(retired, key=weight)
    tc = titles.get(r["individual_id"], Counter())
    if weight(r) < 15:
        return None
    rid, name = r["individual_id"], r["display_name"]
    archived = next((x for x in ctx["state"].get("retired_archive", []) if x.get("id") == rid), {})
    if r["win"] + r["loss"] + r.get("draw", 0) > 0:
        lines = [f"{name}が今季限りで引退した（今季は{r['league']}リーグで{_record(r['win'], r['loss'], r.get('draw', 0))}）。"]
    else:
        # 青龍在位者はAリーグの総当たりを免除される（防衛専念枠）。0勝0敗をわざわざ書かせない
        lines = [f"{name}が今季限りで引退した。"]
    held = [t for t, info in (ctx["state"].get("titleholders") or {}).items() if info and info.get("id") == rid]
    if held:
        lines.append(f"{name}は引退の時点で{ '・'.join(held) }のタイトルを保持していた。")
    if archived.get("retirement_reason"):
        lines.append(f"引退の理由：{archived['retirement_reason']}。")
    if tc:
        lines.append(f"{name}の通算タイトル獲得：" + "、".join(f"{t}{n}期" for t, n in tc.items()) + "。")
    if archived.get("total_seasons"):
        lines.append(f"{name}の在籍は通算{archived['total_seasons']}季。")
    others = [x for x in retired if x is not r]
    if others:
        lines.append("今季はほかに" + "、".join(x["display_name"] for x in others[:4]) + "も引退した。")
    return dict(topic="引退", score=40 + weight(r) // 2, lines=lines, subject=f"{name}の引退",
                tags=["コラム", "引退"], related_individual_ids=[rid], related_individual_names=[name])


def topic_riser(ctx):
    """成長株：今季レートを大きく上げた選手（前季比）。覚醒した新人も含む"""
    season = ctx["season"]
    prev = {r["individual_id"]: r["elo"] for r in _main_rows(ctx["standings"].get(season - 1, []))}
    best = None
    for r in _main_rows(ctx["standings"][season]):
        if r["individual_id"] not in prev:
            continue
        gain = r["elo"] - prev[r["individual_id"]]
        if best is None or gain > best[0]:
            best = (gain, r)
    if best is None or best[0] < 60:
        return None
    gain, r = best
    rid, name = r["individual_id"], r["display_name"]
    lines = [f"{name}（{r['league']}リーグ）は今季{_record(r['win'], r['loss'], r.get('draw', 0))}で、"
             f"レートを前季から約{round(gain)}上げた（今季終了時{round(r['elo'])}）。"]
    if r.get("movement") == "promoted" or "promoted" in (r.get("movement") or ""):
        lines.append(f"{name}は来季、上のリーグへ昇級する。")
    return dict(topic="成長株", score=30 + int(gain // 4), lines=lines, subject=f"{name}の躍進",
                tags=["コラム", f"{r['league']}リーグ"], related_individual_ids=[rid], related_individual_names=[name])


def topic_form(ctx):
    """調子：実力者（Aリーグ在籍・タイトル保持者・レート上位10人）の絶不調／絶好調。
    下位の選手の不調はニュースにならないので取り上げない"""
    season = ctx["season"]
    rows = _main_rows(ctx["standings"][season])
    top_ids = {r["individual_id"] for r in sorted(rows, key=lambda r: -r["elo"])[:10]}
    holder_ids = {info.get("id") for info in (ctx["state"].get("titleholders") or {}).values() if info}
    best = None
    for r in rows:
        rid = r["individual_id"]
        if not (r["league"] == "A" or rid in top_ids or rid in holder_ids):
            continue
        form = ctx["params"].get(rid, {}).get("_form")
        games = r["win"] + r["loss"]
        if form is None or games < 6:
            continue
        rate = r["win"] / games
        if form <= -3 and rate <= 0.35:
            s, kind = 35 + int((0.5 - rate) * 80), "絶不調"
        elif form >= 3 and rate >= 0.8:
            s, kind = 30 + int((rate - 0.5) * 60), "絶好調"
        else:
            continue
        if best is None or s > best[0]:
            best = (s, r, kind)
    if best is None:
        return None
    s, r, kind = best
    rid, name = r["individual_id"], r["display_name"]
    status = "タイトル保持者" if rid in holder_ids else ("Aリーグ" if r["league"] == "A" else "レート上位")
    lines = [f"{name}（{r['league']}リーグ、{status}の実力者）は今季{'不振' if kind == '絶不調' else '好調'}で、"
             f"成績は{_record(r['win'], r['loss'], r.get('draw', 0))}だった。"]
    return dict(topic="調子", score=s, lines=lines, subject=f"実力者{name}の{'不振' if kind == '絶不調' else '快進撃'}",
                tags=["コラム", f"{r['league']}リーグ"], related_individual_ids=[rid], related_individual_names=[name])


def topic_fall(ctx):
    """昇降級ドラマ：タイトル経験者のAリーグからの陥落"""
    season, titles = ctx["season"], ctx["title_counts"]
    cands = [r for r in _main_rows(ctx["standings"][season])
             if r["league"] == "A" and r.get("movement") == "relegated" and titles.get(r["individual_id"])]
    if not cands:
        return None
    r = max(cands, key=lambda x: sum(titles[x["individual_id"]].values()))
    rid, name = r["individual_id"], r["display_name"]
    tc = titles[rid]
    lines = [f"タイトル通算{sum(tc.values())}期（" + "、".join(f"{t}{n}期" for t, n in tc.items()) + f"）の{name}が、"
             f"今季Aリーグで{_record(r['win'], r['loss'], r.get('draw', 0))}に終わり、来季はBリーグへ陥落する。"]
    return dict(topic="陥落", score=45 + sum(tc.values()) * 2, lines=lines, subject=f"{name}のAリーグ陥落",
                tags=["コラム", "Aリーグ"], related_individual_ids=[rid], related_individual_names=[name])


def topic_clan(ctx):
    """一門：今季、一門から独立して新しい一門を興した選手"""
    season = ctx["season"]
    evs = [e for e in ctx["state"].get("clan_independence_history", []) if e.get("season") == season]
    if not evs:
        return None
    e = evs[0]
    founder = ctx["names"].get(e.get("from_clan_id"), e.get("from_clan_id"))
    lines = [f"{e['name']}が、弟子・孫弟子を引き連れて{founder}一門から独立し、自らを開祖とする一門を興した。"]
    return dict(topic="一門", score=55, lines=lines, subject=f"{e['name']}一門の独立",
                tags=["コラム", "一門"], related_individual_ids=[e["id"]], related_individual_names=[e["name"]])


def topic_title_milestone(ctx):
    """記録：今季のタイトル獲得で、通算タイトル数が節目（10・15・20…期）に達した選手"""
    season, state = ctx["season"], ctx["state"]
    before, after = _title_counts(state, season - 1), ctx["title_counts"]
    best = None
    for pid, c in after.items():
        n_after, n_before = sum(c.values()), sum(before.get(pid, {}).values())
        for mark in (10, 15, 20, 25, 30):
            if n_before < mark <= n_after and (best is None or mark > best[0]):
                best = (mark, pid, n_after)
    if best is None:
        return None
    mark, pid, n = best
    name = ctx["names"].get(pid, pid)
    lines = [f"{name}が今季のタイトル獲得で、通算タイトル獲得数が{n}期に達した。",
             "内訳：" + "、".join(f"{t}{k}期" for t, k in after[pid].items()) + "。"]
    return dict(topic="記録", score=50 + mark, lines=lines, subject=f"{name}の通算{mark}期",
                tags=["コラム", "記録"], related_individual_ids=[pid], related_individual_names=[name])


def topic_first_title(ctx):
    """初戴冠：今季の番勝負で、それまでタイトル獲得の無かった選手が初めてタイトルを獲った"""
    season, state = ctx["season"], ctx["state"]
    before = _title_counts(state, season - 1)
    best = None
    for e in state.get("title_history", []):
        if e.get("season") != season or not e.get("won"):
            continue
        cid = e.get("challenger_id")
        if not cid or sum(before.get(cid, {}).values()) > 0:
            continue
        score = 55 + {"青龍": 15, "朱雀": 10, "白虎": 5, "玄武": 0}.get(e["title"], 0)
        if best is None or score > best[0]:
            best = (score, e)
    if best is None:
        return None
    score, e = best
    cid = e["challenger_id"]; name = ctx["names"].get(cid, e.get("challenger_name", cid))
    holder = ctx["names"].get(e.get("defender_id"), "") if e.get("defender_id") else ""
    lines = [f"{name}が{e['title']}戦の番勝負で" + (f"{holder}を" if holder else "") +
             f"{e.get('challenger_wins')}勝{e.get('titleholder_wins')}敗で破り、初めてタイトルを獲得した。"]
    nth = ctx["seasons_played"].get(cid)
    if nth:
        lines.append(f"{name}にとっては在籍{nth}季目での初タイトルだった。")
    return dict(topic="初戴冠", score=score, lines=lines, subject=f"{name}の初タイトル",
                tags=["コラム", e["title"]], related_individual_ids=[cid] + ([e["defender_id"]] if e.get("defender_id") else []),
                related_individual_names=[name] + ([holder] if holder else []))


def topic_master_disciple(ctx):
    """師弟対決：今季、師匠と弟子が直接対局した（番勝負・挑戦者決定の対局を優先）"""
    season, parents = ctx["season"], ctx["parents"]
    best = None
    for m in ctx["matches"][season]:
        a, b, r = m.get("individual_a_id"), m.get("individual_b_id"), m.get("result")
        if not (a and b) or r not in ("win", "loss"):
            continue
        if parents.get(a) == b:
            master, disciple = b, a
        elif parents.get(b) == a:
            master, disciple = a, b
        else:
            continue
        winner = a if r == "win" else b
        big = m.get("league") not in LEAGUES
        score = 35 + (20 if big else 0) + (10 if winner == disciple else 0)
        if best is None or score > best[0]:
            best = (score, master, disciple, winner, m.get("league"))
    if best is None:
        return None
    score, master, disciple, winner, league = best
    nm, nd = ctx["names"].get(master, master), ctx["names"].get(disciple, disciple)
    lines = [f"{ctx['stage_label'](league)}で、師匠の{nm}と弟子の{nd}が対局し、{ctx['names'].get(winner, winner)}が勝った。"]
    if winner == disciple:
        lines.append(f"弟子の{nd}が師匠に勝った（恩返し）。")
    return dict(topic="師弟対決", score=score, lines=lines, subject=f"師匠{nm}と弟子{nd}の対局",
                tags=["コラム", "師弟"], related_individual_ids=[master, disciple], related_individual_names=[nm, nd])


TOPIC_FUNCS = [topic_rivalry, topic_retirement, topic_riser, topic_form, topic_fall, topic_clan, topic_title_milestone,
               topic_first_title, topic_master_disciple]


def build_context(data_dir, season, stage_label):
    state = _load(os.path.join(data_dir, "season_state.json"), {})
    rosters = _load(os.path.join(data_dir, "rosters.json"), {})
    params = {i["id"]: i.get("params", {}) for v in rosters.values() for i in v}
    standings = {s: _standings(data_dir, s) for s in (season - 1, season)}
    matches = {s: _matches(data_dir, s) for s in range(1, season + 1)}
    names = {}
    for x in state.get("retired_archive", []):
        names[x["id"]] = x.get("display_name", x["id"])
    for v in rosters.values():
        for i in v:
            names[i["id"]] = i.get("display_name", i["id"])
    for rows in standings.values():
        for r in rows:
            names[r["individual_id"]] = r["display_name"]
    parents = {i["id"]: i.get("parent_a_id") for v in rosters.values() for i in v}
    parents.update({x["id"]: x.get("parent_a_id") for x in state.get("retired_archive", []) if x["id"] not in parents})
    seasons_played = Counter()
    for s in range(1, season + 1):
        for r in _main_rows(_standings(data_dir, s)) if s not in standings else _main_rows(standings[s]):
            seasons_played[r["individual_id"]] += 1
    return dict(season=season, state=state, params=params, standings=standings, matches=matches, names=names,
                title_counts=_title_counts(state, season), stage_label=stage_label, parents=parents,
                seasons_played=seasons_played)


def recent_topics(articles, season):
    """直近RECENT_TOPIC_SEASONS季に使ったコラムのテーマ"""
    used = set()
    for a in articles:
        s = a.get("season") or 0
        if a.get("topic") and season - RECENT_TOPIC_SEASONS <= s < season:
            used.add(a["topic"])
    return used


def pick_format(articles, season, offset=0):
    """書き方の型を持ち回りで選ぶ（季と記事の順番で決める）"""
    return FORMATS[(season * 2 + offset) % len(FORMATS)]


def recent_titles(articles, n=20):
    return [a["title"] for a in sorted(articles, key=lambda a: a.get("published_at") or "")[-n:] if a.get("title")]
