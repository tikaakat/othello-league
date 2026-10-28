"""
ニュース記事生成：直近のシーズン結果からダイジェスト記事を1本生成し、
data/news/articles.json へ追記する。文章はClaude Code CLI（`claude -p`、
ヘッドレス実行）で生成し、news-site/persona.md の内容をsystemプロンプトと
して渡すことで、記者としての人格・文体を毎回一貫させる。

API従量課金ではなく、Claude Pro/Max/Team/Enterpriseサブスクリプションの
利用枠を使う（CLAUDE_CODE_OAUTH_TOKEN環境変数、`claude setup-token`で発行。
GitHub Secretsに登録して渡す想定）。ツールは一切使わせない（--tools ""）、
純粋なテキスト生成としてCLIを呼び出す。

対象は「まだダイジェストが無い最新シーズン」。既に生成済みのシーズンは
再生成しない（articles.jsonにそのシーズンのidが無ければ対象、あれば
スキップ）。本戦（run_season.py）とは別プロセス・別スケジュールで動かす想定
（本戦の実行が終わり、data/standings・data/matchesが更新された後に呼ぶ）。
"""
import argparse
import datetime
import json
import os
import subprocess

import news_topics

DEFAULT_MODEL = "sonnet"
TITLE_NAMES = ["麒麟", "青龍", "朱雀", "白虎", "玄武"]
# タイトルの格（麒麟・青龍＝竜王・名人格が最高、玄武が最も格下）。MVPは今季獲得・防衛したタイトルの
# この点数の合計で選ぶ。青龍は最高格だが、単冠が二冠（最低でも玄武3＋白虎4＝7）を
# 上回らないよう、各タイトルの差を小さくしてある（4/3/2/1だと青龍単冠4が白虎＋玄武の3を上回っていた）
TITLE_PRESTIGE = {"麒麟": 6, "青龍": 6, "朱雀": 5, "白虎": 4, "玄武": 3}
_NEWS_SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news-site")
PERSONA_PATH = os.path.join(_NEWS_SITE_DIR, "persona.md")
# コラム・特集は、ダイジェスト（犬飼）とは別の記者（東堂）が書く
COLUMN_PERSONA_PATH = os.path.join(_NEWS_SITE_DIR, "persona-column.md")
AUTHOR_DIGEST = "犬飼"
AUTHOR_COLUMN = "東堂アヤ"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_gender_map(data_dir):
    """個体ID→性別（"male"/"female"/未設定ならNone）の辞書を、現役（rosters.json）・
    引退済み（season_state.jsonのretired_archive）の両方から組み立てる。記事生成の
    プロンプトに明示的に渡すことで、モデルが性別を当て推量して「この男」「彼女」等と
    誤記する（ファンサイトのDiceBearアイコンの性別と食い違う）のを防ぐ"""
    gender_by_id = {}
    rosters = load_json(os.path.join(data_dir, "rosters.json"), {})
    for league_members in rosters.values():
        for ind in league_members:
            gender_by_id[ind["id"]] = ind.get("gender")
    state = load_json(os.path.join(data_dir, "season_state.json"), {})
    for r in state.get("retired_archive", []):
        gender_by_id.setdefault(r["id"], r.get("gender"))
    return gender_by_id


def _gender_label(gender):
    return {"male": "男性", "female": "女性"}.get(gender, "不明")


# 各タイトルの挑戦者（初代の場合は襲名者）がどう決まるか。記事生成AIにこれを渡さないと、
# 例えば「Aリーグ1位だから青龍を襲名した」のに「青龍襲名に加え、Aリーグでも好成績」のように
# 同じ一つの実績を別々の功績として並べてしまう
TITLE_QUALIFY_PATH = {
    "麒麟": ("麒麟戦の挑戦者決定戦の勝者（麒麟戦独自の組分けで、1組は本戦から、2組・3組は組ごとの予選トーナメントを"
             "勝ち抜いて本戦16名のトーナメントへ。ベスト4からは2敗で失格の敗者復活戦があり、本戦優勝者は1勝、"
             "敗者復活戦の勝者は2連勝で挑戦権を得る）"),
    "青龍": "Aリーグ（青龍戦リーグ）の総当たり1位",
    "朱雀": "朱雀紅白リーグの紅組1位・白組1位による挑戦者決定戦の勝者",
    "白虎": "Elo上位16名による白虎トーナメントの優勝者",
    "玄武": "全選手参加の玄武戦（4ブロックのトーナメント。タイトル保持者は3回戦から、Aリーグの選手は2回戦から登場するシードあり→ブロック優勝者による挑戦者決定リーグ）の1位",
}
TITLE_RULES_NOTE = (
    "【タイトルの決まり方】"
    + "".join(f"{t}は{p}が挑戦者になる。" for t, p in TITLE_QUALIFY_PATH.items())
    + "在位者がいない新設タイトル（初代）は、この挑戦者決定の勝者が番勝負なしでそのまま襲名する。"
    "在位者は挑戦者決定（リーグ・トーナメント）には出場せず、勝ち上がってきた挑戦者を番勝負で迎え撃つ。"
    "防衛とはこの番勝負に勝つことであり、在位者が毎季挑戦者決定を勝ち抜いているかのように書かないこと。"
    "挑戦権（または初代襲名）を得るまでの成績（例：青龍ならAリーグの成績）と、タイトルの獲得・挑戦は"
    "ひと続きの同じ実績である。「〜に加え」「〜でも」のように別々の功績として並べたり、"
    "「称号を受け取っただけでなく盤上でも実力を示した」のように切り離して書かないこと。"
)

GENDER_PROMPT_NOTE = (
    "各選手の性別はデータ中の「性別」欄の通りとし、不明な場合を除き、"
    "それに矛盾する表現（性別不明なのに「彼」「彼女」と決めつける等）をしないこと。"
)


def latest_season_with_standings(data_dir):
    standings_dir = os.path.join(data_dir, "standings")
    if not os.path.isdir(standings_dir):
        return None
    seasons = []
    for fn in os.listdir(standings_dir):
        if fn.startswith("season_") and fn.endswith(".json"):
            try:
                seasons.append(int(fn[len("season_"):-len(".json")]))
            except ValueError:
                continue
    return max(seasons) if seasons else None


def _pick_best_record(rows):
    """standingsの行（A〜Dリーグのみを想定）の中から、最も勝数が多い個体を選ぶ
    （同数なら勝数−敗数が大きい方、さらに同じならAリーグ優先・個体ID昇順で決定的にする）。
    該当者がいなければNoneを返す"""
    if not rows:
        return None
    league_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    return max(
        rows,
        key=lambda r: (r.get("win", 0), r.get("win", 0) - r.get("loss", 0),
                       -league_rank.get(r.get("league"), 9), r["individual_id"]),
    )


def _pick_mvp(title_results, main_league_rows):
    """今季のMVPを選ぶ。勝数だけでなく、タイトルを奪取・防衛（初代襲名も含む）した
    個体を優先する（勝数集計だけでは、防衛専念枠で本戦総当たりを免除され win=0に
    なる在位者が評価から漏れてしまうため）。該当者が複数いる場合は、タイトルの格
    （青龍＞朱雀＞白虎＞玄武）が高い方、さらに同格なら本戦の勝数が多い方を選ぶ。
    今季タイトル絡みの実績が誰にも無ければ、従来通り最多勝の個体にフォールバックする"""
    win_by_id = {r["individual_id"]: r.get("win", 0) for r in main_league_rows}
    loss_by_id = {r["individual_id"]: r.get("loss", 0) for r in main_league_rows}
    draw_by_id = {r["individual_id"]: r.get("draw", 0) for r in main_league_rows}

    achievers = {}  # individual_id -> {"name", "score", "titles": [(title, result, match_score), ...]}
    for tr in title_results:
        wid, wname = tr["winner_id"], tr["winner_name"]
        entry = achievers.setdefault(wid, {"name": wname, "score": 0, "titles": []})
        entry["score"] += TITLE_PRESTIGE.get(tr["title"], 0)
        # タイトル戦の対局成績（例："3-1"）も合わせて持たせる。青龍在位者のように
        # リーグ戦が防衛専念枠で免除され、リーグ成績が0勝0敗になる個体がいるため、
        # 記事側で「0勝0敗なのに受賞」という誤解を招く表記を避けるのに使う。
        # 初代襲名は番勝負自体が無いため、対局成績の文字列は持たせない
        # 対局成績はMVP本人（勝者）側から見た「自分の勝ち数-相手の勝ち数」で持たせる
        # （挑戦者側から見た値のままだと、防衛した在位者が「玄武防衛（0-3）」のように負けたかに読める）
        if tr["result"] == "初代襲名":
            match_score = None
        elif tr["result"] == "防衛":
            match_score = f"{tr['holder_wins']}-{tr['challenger_wins']}"
        else:
            match_score = f"{tr['challenger_wins']}-{tr['holder_wins']}"
        entry["titles"].append((tr["title"], tr["result"], match_score))

    if achievers:
        best_id = max(achievers, key=lambda iid: (achievers[iid]["score"], win_by_id.get(iid, 0), iid))
        chosen = achievers[best_id]
        return {
            "individual_id": best_id, "name": chosen["name"], "titles": chosen["titles"],
            "league": next((r["league"] for r in main_league_rows if r["individual_id"] == best_id), None),
            "win": win_by_id.get(best_id, 0), "loss": loss_by_id.get(best_id, 0),
            "draw": draw_by_id.get(best_id, 0),
        }

    fallback = _pick_best_record(main_league_rows)
    if fallback is None:
        return None
    return {
        "individual_id": fallback["individual_id"], "name": fallback["display_name"], "titles": [],
        "league": fallback["league"], "win": fallback.get("win", 0), "loss": fallback.get("loss", 0),
        "draw": fallback.get("draw", 0),
    }


ROOKIE_MAX_AGE = 26   # 新人王の対象年齢（シーズン開始時点でこれ以下）
ROOKIE_MAX_DAN = 6    # 新人王の対象段位（シーズン開始時点でこれ以下）
# タイトル挑戦・獲得による新人王の評価点（勝数・勝率よりはるかに大きく重み付けし、
# 「タイトル絡みの実績がある方が、単なる勝数より評価されるべき」という方針を徹底する）
ROOKIE_TITLE_CAPTURE_SCORE = 1000
ROOKIE_TITLE_CHALLENGE_SCORE = 100


def _dan_league_history(data_dir, up_to_season_exclusive):
    """1〜up_to_season_exclusive-1季のstandingsから、個体ごとの(season, league, win)の
    履歴（season昇順）を組み立てる。段位（floor＋勝数による昇段のみ。タイトル絡みの
    昇段条件は、新人王の対象をそもそも「タイトル戦経験が無い個体」に絞るため不要）の
    簡易シミュレーションに使う"""
    history = {}
    for s in range(1, up_to_season_exclusive):
        rows = load_json(os.path.join(data_dir, "standings", f"season_{s}.json"), [])
        for r in rows:
            league = r.get("league")
            if league not in ("A", "B", "C", "D"):
                continue
            history.setdefault(r["individual_id"], []).append((s, league, r.get("win", 0)))
    return history


def _title_experience_ids(data_dir, up_to_season_exclusive):
    """1〜up_to_season_exclusive-1季の対局から、タイトル戦（本戦。予選トーナメントは
    対象外＝site/api.phpの段位計算における「タイトル挑戦」の定義と揃える）に挑戦者・
    保持者として登場した個体のIDの集合を作る"""
    ids = set()
    for s in range(1, up_to_season_exclusive):
        rows = load_json(os.path.join(data_dir, "matches", f"season_{s}.json"), [])
        for m in rows:
            if m.get("league") in TITLE_NAMES:
                ids.add(m["individual_a_id"])
                ids.add(m["individual_b_id"])
    return ids


def _simplified_dan(history):
    """タイトル戦経験が無い個体限定の簡易段位シミュレーション（site/api.phpの
    simulate_dan_progressionのうち、タイトル絡みの昇段条件を省いたもの。
    対象をタイトル戦経験の無い個体に絞っているため、省いても結果は変わらない）。
    historyは(season, league, win)のリスト（season昇順）"""
    dan_league_floor = {"C": 5, "B": 6, "A": 7}
    dan_win_step = {5: 40, 6: 50, 7: 60, 8: 80, 9: 100}  # site/api.phpのDAN_WIN_STEPと揃える
    dan = 4  # 新人の初期段位
    win_baseline = 0
    cum_win = 0
    reached = set()
    for _season, league, win in history:
        cum_win += win
        floor = dan_league_floor.get(league)
        if floor is not None and league not in reached:
            reached.add(league)
            if floor > dan:
                dan = floor
                win_baseline = cum_win
        while dan < 9:
            step = dan_win_step.get(dan + 1)
            if step is None or (cum_win - win_baseline) < step:
                break
            win_baseline += step
            dan += 1
    return dan


def _career_before(data_dir, season):
    """1〜season-1季のstandingsから、個体ごとの「今季より前に在籍した季数」と
    「初めて在籍した季・リーグ」を求める。rosters.jsonのtotal_seasonsは最新季終了時点の値のため、
    過去の季の記事を作り直す際にそのまま使うと季数・年齢がずれてしまう（今季終了時点でも
    今季分が加算済みで1つずれる）。standingsの履歴から数えればどの季でも正しく求まる"""
    prior = {}
    first = {}
    for s in range(1, season):
        rows = load_json(os.path.join(data_dir, "standings", f"season_{s}.json"), [])
        for r in rows:
            if r.get("league") not in ("A", "B", "C", "D"):
                continue
            iid = r["individual_id"]
            prior[iid] = prior.get(iid, 0) + 1
            first.setdefault(iid, (s, r["league"]))
    return prior, first


def _initial_age_map(data_dir):
    """個体ID→初年度の年齢（initial_age）。現役（rosters.json）と引退済み（retired_archive）の両方から"""
    ages = {}
    for league_members in load_json(os.path.join(data_dir, "rosters.json"), {}).values():
        for ind in league_members:
            ages[ind["id"]] = ind.get("initial_age")
    for r in load_json(os.path.join(data_dir, "season_state.json"), {}).get("retired_archive", []):
        ages.setdefault(r["id"], r.get("initial_age"))
    return ages


def _career_label(nth_season):
    return "今季がデビューシーズン（通算1季目）" if nth_season == 1 else f"通算{nth_season}季目（デビューシーズンではない）"


def _pick_rookie_of_year(data_dir, season, rosters, main_league_rows, title_results):
    """新人王を選ぶ。対象は「今季デビューの新規参入者」ではなく（Dリーグ新人受け入れが
    毎季固定少数（D_NEWCOMER_INTAKE）のため、そこだけに絞ると選択肢が狭すぎる）、シーズン開始時点で
    ①26歳以下 ②六段以下 ③タイトル戦経験が無い、という条件を満たす個体全員。
    その中から、今季タイトルを奪取・挑戦した個体を最優先し（新人の年齢・段位で
    タイトル戦に絡めば十分な実績と言えるため）、それが無ければ勝率→勝数で最も
    活躍した個体を選ぶ。該当者がいなければNoneを返す"""
    # シーズン開始時点の年齢＝初年度の年齢＋今季より前の在籍季数
    # （rosters.jsonのtotal_seasonsは最新季終了時点の値のため使わない。_career_before参照）
    prior_seasons, _ = _career_before(data_dir, season)
    initial_age = _initial_age_map(data_dir)
    age_by_id = {
        iid: initial_age[iid] + prior_seasons.get(iid, 0)
        for iid in (r["individual_id"] for r in main_league_rows)
        if initial_age.get(iid) is not None
    }
    title_exp_ids = _title_experience_ids(data_dir, season)
    league_history = _dan_league_history(data_dir, season)

    eligible_ids = set()
    for r in main_league_rows:
        iid = r["individual_id"]
        age = age_by_id.get(iid)
        if age is None or age > ROOKIE_MAX_AGE:
            continue
        if iid in title_exp_ids:
            continue
        if _simplified_dan(league_history.get(iid, [])) > ROOKIE_MAX_DAN:
            continue
        eligible_ids.add(iid)
    if not eligible_ids:
        return None

    title_score_by_id = {}
    for tr in title_results:
        cid = tr.get("challenger_id")
        if cid not in eligible_ids:
            continue
        # 初代襲名（在位者不在のため無条件で襲名）も、タイトルを獲得した点では
        # 奪取と同等の実績なので同じスコアを与える
        score = (ROOKIE_TITLE_CAPTURE_SCORE if tr["result"] in ("奪取", "初代襲名") else ROOKIE_TITLE_CHALLENGE_SCORE) \
            + TITLE_PRESTIGE.get(tr["title"], 0)
        if score > title_score_by_id.get(cid, 0):
            title_score_by_id[cid] = score

    rows_by_id = {r["individual_id"]: r for r in main_league_rows}

    def sort_key(iid):
        row = rows_by_id.get(iid, {})
        win, loss = row.get("win", 0), row.get("loss", 0)
        win_rate = win / (win + loss) if (win + loss) else 0.0
        return (title_score_by_id.get(iid, 0), win_rate, win, iid)

    best_id = max(eligible_ids, key=sort_key)
    row = rows_by_id.get(best_id, {})
    tr = next((t for t in title_results if t.get("challenger_id") == best_id), None)
    return {
        "individual_id": best_id, "name": row.get("display_name", best_id),
        "league": row.get("league"), "win": row.get("win", 0), "loss": row.get("loss", 0),
        "draw": row.get("draw", 0), "age": age_by_id.get(best_id),
        "title": tr["title"] if tr else None, "title_result": tr["result"] if tr else None,
    }


def _lineage_map(data_dir):
    """個体ID→{name, master_id, clan_root_id}（現役・引退済みとも）"""
    out = {}
    for league_members in load_json(os.path.join(data_dir, "rosters.json"), {}).values():
        for ind in league_members:
            out[ind["id"]] = {"name": ind.get("display_name"), "master_id": ind.get("parent_a_id"),
                              "clan_root_id": ind.get("clan_root_id")}
    for r in load_json(os.path.join(data_dir, "season_state.json"), {}).get("retired_archive", []):
        out.setdefault(r["id"], {"name": r.get("display_name"), "master_id": r.get("parent_a_id"),
                                 "clan_root_id": r.get("clan_root_id")})
    return out


def _lineage_relation(lineage, challenger_id, holder_id):
    """タイトル戦の挑戦者と保持者の師弟・同門関係を、記事に盛り込める一文にする（無ければNone）。
    一門は現在の所属（独立後の一門）で判定する"""
    c, h = lineage.get(challenger_id), lineage.get(holder_id)
    if not c or not h:
        return None
    cn, hn = c["name"], h["name"]
    if c["master_id"] == holder_id:
        return f"師弟対決：挑戦者{cn}は保持者{hn}の弟子"
    if h["master_id"] == challenger_id:
        return f"師弟対決：保持者{hn}は挑戦者{cn}の弟子"
    if c["master_id"] and c["master_id"] == h["master_id"]:
        master = lineage.get(c["master_id"], {}).get("name") or "同じ師匠"
        return f"兄弟弟子対決：両者とも{master}の弟子"
    if c["clan_root_id"] and c["clan_root_id"] == h["clan_root_id"]:
        founder = lineage.get(c["clan_root_id"], {}).get("name") or "同じ開祖"
        return f"同門対決：両者とも{founder}一門"
    return None


def gather_season_facts(data_dir, season):
    """指定シーズンの対局結果から、記事生成に使う構造化データ（事実）を集める。
    タイトル戦の勝者側（奪取なら挑戦者、防衛ならホルダー）のIDを
    related_individual_idsとしてまとめて返す（記事の「関連個体」表示用）"""
    standings = load_json(os.path.join(data_dir, "standings", f"season_{season}.json"), [])
    matches = load_json(os.path.join(data_dir, "matches", f"season_{season}.json"), [])
    rosters = load_json(os.path.join(data_dir, "rosters.json"), {})
    names = {r["individual_id"]: r["display_name"] for r in standings}

    main_league_rows = [r for r in standings if r.get("league") in ("A", "B", "C", "D")]
    gender_by_id = _load_gender_map(data_dir)

    # 「初代襲名」（まだ誰も保持したことのないタイトルへ、在位者不在のため無条件で
    # 即位する）の検出用。この場合は番勝負自体が発生しないため、matchesには
    # 該当タイトルの対局が1件も記録されない（下のループで通常のrowsが空になる）
    state = load_json(os.path.join(data_dir, "season_state.json"), {})
    inaugural_by_title = {
        e["title"]: e for e in state.get("title_history", [])
        if e.get("season") == season and e.get("event") == "初代襲名"
    }

    title_results = []
    related = []  # [(id, name), ...]（勝者のみ、重複は後で除去）
    lineage = _lineage_map(data_dir)
    for t in TITLE_NAMES:
        rows = [m for m in matches if m.get("league") == t]
        if not rows:
            inaugural = inaugural_by_title.get(t)
            if inaugural is not None:
                holder_id = inaugural.get("holder_id")
                holder_name = inaugural.get("new_holder") or names.get(holder_id, holder_id)
                title_results.append({
                    "title": t, "challenger_id": holder_id, "holder_id": None,
                    "winner_id": holder_id, "winner_name": holder_name,
                    "winner_gender": gender_by_id.get(holder_id),
                    "challenger_name": holder_name, "holder_name": None,
                    "challenger_wins": None, "holder_wins": None,
                    "result": "初代襲名",
                })
                related.append((holder_id, holder_name))
            continue
        a_id, b_id = rows[0]["individual_a_id"], rows[0]["individual_b_id"]
        relation = _lineage_relation(lineage, a_id, b_id)
        win_a = sum(1 for r in rows if r["result"] == "win")
        loss_a = sum(1 for r in rows if r["result"] == "loss")
        challenger_won = win_a > loss_a
        winner_id = a_id if challenger_won else b_id
        title_results.append({
            "title": t, "challenger_id": a_id, "holder_id": b_id,
            "winner_id": winner_id, "winner_name": names.get(winner_id, winner_id),
            "winner_gender": gender_by_id.get(winner_id),
            "challenger_name": names.get(a_id, a_id), "holder_name": names.get(b_id, b_id),
            "challenger_wins": win_a, "holder_wins": loss_a,
            "result": "奪取" if challenger_won else "防衛",
            "lineage_relation": relation,
        })
        related.append((winner_id, names.get(winner_id, winner_id)))

    # 勝者がそのタイトルを保持するのは通算何期目か（「初戴冠」「〜連覇」等を当て推量で書かせないため）
    title_history = state.get("title_history", [])
    for tr in title_results:
        tr["winner_title_total"], _ = compute_title_stats_up_to(title_history, tr["title"], tr["winner_name"], season)
    # 今季終了時点で各個体が保持しているタイトル（全タイトルが毎季決着するので、今季の勝者＝季末の保持者）
    crowns_by_id = {}
    for tr in title_results:
        crowns_by_id.setdefault(tr["winner_id"], []).append(tr["title"])

    prior_seasons, _ = _career_before(data_dir, season)

    mvp = _pick_mvp(title_results, main_league_rows)
    if mvp is not None:
        mvp["gender"] = gender_by_id.get(mvp["individual_id"])
        mvp["nth_season"] = prior_seasons.get(mvp["individual_id"], 0) + 1
        mvp["crowns"] = crowns_by_id.get(mvp["individual_id"], [])
        related.append((mvp["individual_id"], mvp["name"]))

    rookie = _pick_rookie_of_year(data_dir, season, rosters, main_league_rows, title_results)
    # MVPと同一人物であれば、新人王は重複して取り上げない
    if rookie is not None and (mvp is None or rookie["individual_id"] != mvp["individual_id"]):
        rookie["gender"] = gender_by_id.get(rookie["individual_id"])
        rookie["nth_season"] = prior_seasons.get(rookie["individual_id"], 0) + 1
        related.append((rookie["individual_id"], rookie["name"]))
    elif rookie is not None:
        rookie = None

    # 昇格・降格は、Aリーグが絡むもの（B→A・A→B）だけ個別に名前を残す。
    # B〜D間の入れ替えは人数が多く記事が名前の列挙だけで埋まってしまうため、件数のみ集計する
    movements = {"promoted_a": [], "relegated_a": [], "promoted_other": 0, "relegated_other": 0,
                 "new": [], "retired": []}
    for r in standings:
        league = r.get("league")
        if league not in ("A", "B", "C", "D"):
            continue
        mv = r.get("movement", "")
        if "promoted" in mv:
            if league == "B":
                movements["promoted_a"].append(r["display_name"])
            else:
                movements["promoted_other"] += 1
        if "relegated" in mv:
            if league == "A":
                movements["relegated_a"].append(r["display_name"])
            else:
                movements["relegated_other"] += 1
        if "new" in mv:
            movements["new"].append(r["display_name"])
        if "retired" in mv:
            movements["retired"].append(r["display_name"])

    seen = set()
    related_unique = []
    for iid, name in related:
        if iid in seen:
            continue
        seen.add(iid)
        related_unique.append((iid, name))

    return {
        "season": season, "title_results": title_results, "movements": movements,
        "mvp": mvp, "rookie": rookie,
        "related_individual_ids": [iid for iid, _ in related_unique],
        "related_individual_names": [name for _, name in related_unique],
    }


def _title_count_label(total):
    if not total:
        return "通算期数不明"
    return "初めて（初獲得）" if total == 1 else f"通算{total}期目"


def _record(win, loss, draw):
    return f"{win}勝{loss}敗" + (f"{draw}分" if draw else "")


# 経歴を当て推量で書かせない（新人王を「デビューシーズン」と書いてしまった不具合の再発防止）
CAREER_PROMPT_NOTE = (
    "選手の経歴（デビュー・初出場・初タイトル・ベテラン・〜連覇等）は、データに明記されたものだけを書くこと。"
    "新人王は「シーズン開始時点で26歳以下・六段以下・タイトル戦（番勝負）未経験」の若手から選ぶ賞であり、"
    "デビュー年の選手に限らない。データで「通算1季目」とされていない限り、「デビュー」「ルーキーイヤー」"
    "「初年度」「初参戦」のように書かないこと。"
)


def build_prompt(facts):
    lines = [f"第{facts['season']}季の結果データ（事実のみ。これ以外の出来事は起きていない）:", "", "■タイトル戦"]
    for tr in facts["title_results"]:
        if tr["result"] == "初代襲名":
            lines.append(
                f"・{tr['title']}：まだ誰も保持したことのない新設タイトルのため在位者が不在。"
                f"{TITLE_QUALIFY_PATH[tr['title']]}となった{tr['winner_name']}"
                f"（性別：{_gender_label(tr['winner_gender'])}）が、番勝負なしでそのまま初代保持者に襲名"
                "（挑戦者・在位者による番勝負は発生していない）"
            )
            continue
        lines.append(
            f"・{tr['title']}：{TITLE_QUALIFY_PATH[tr['title']]}として挑戦した"
            f"{tr['challenger_name']} {tr['challenger_wins']}-{tr['holder_wins']} "
            f"保持者{tr['holder_name']} → {tr['result']}（{tr['result']}した側（{tr['winner_name']}）の性別：{_gender_label(tr['winner_gender'])}。"
            f"{tr['winner_name']}の{tr['title']}保持は{_title_count_label(tr.get('winner_title_total'))}"
            + (f"。{tr['lineage_relation']}" if tr.get("lineage_relation") else "")
            + "）"
        )
    mv = facts["movements"]
    lines.append("")
    lines.append("■リーグ戦")
    if mv["promoted_a"]:
        lines.append(f"・Aリーグへ昇格：{'、'.join(mv['promoted_a'])}")
    if mv["relegated_a"]:
        lines.append(f"・Aリーグから降格：{'、'.join(mv['relegated_a'])}")
    if mv["promoted_other"] or mv["relegated_other"]:
        lines.append(
            f"・B〜Dリーグ内の入れ替え：昇格{mv['promoted_other']}名、降格{mv['relegated_other']}名"
            "（個別の名前のデータはここでは渡していない）"
        )
    if facts["season"] == 1:
        lines.append("・第1季のため全選手が初年度（新規参入として個別に紹介する必要はない）")
    elif mv["new"]:
        lines.append(f"・新規参入：{'、'.join(mv['new'])}")
    if mv["retired"]:
        lines.append(f"・引退：{'、'.join(mv['retired'])}")

    mvp, rookie = facts.get("mvp"), facts.get("rookie")
    if mvp or rookie:
        lines.append("")
        lines.append("■今季の活躍")
        if mvp:
            if mvp["titles"]:
                titles_text = "・".join(
                    f"{t}{r}（在位者不在のため番勝負なしで襲名）" if s is None else f"{t}{r}（対局成績{s}）"
                    for t, r, s in mvp["titles"]
                )
                # 青龍在位者のように防衛専念枠でリーグ戦（A〜D）を免除され、
                # リーグ成績が0勝0敗になる個体については、「0勝0敗なのに受賞」という
                # 誤解を招く書き方にならないよう、リーグ成績の記載自体を省く
                # （タイトル戦の対局成績は上のtitles_textに既に含まれている）
                crowns = mvp.get("crowns") or []
                if len(crowns) >= 2:
                    titles_text += f"。今季終了時点で{'・'.join(crowns)}の{len(crowns)}冠"
                career = _career_label(mvp["nth_season"]) if mvp.get("nth_season") else ""
                if career:
                    titles_text += f"。{career}"
                if mvp["win"] == 0 and mvp["loss"] == 0 and not mvp.get("draw"):
                    lines.append(f"・今季MVP：{mvp['name']}（性別：{_gender_label(mvp['gender'])}。{titles_text}。{mvp['league']}リーグは防衛専念枠のため対局免除）")
                else:
                    via_a_league = mvp["league"] == "A" and any(t == "青龍" for t, _, _ in mvp["titles"])
                    note = "。このAリーグ1位の成績によって青龍の挑戦権（または初代襲名）を得たもので、別の功績ではない" \
                        if via_a_league else ""
                    lines.append(
                        f"・今季MVP：{mvp['name']}（性別：{_gender_label(mvp['gender'])}。{titles_text}。{mvp['league']}リーグ{_record(mvp['win'], mvp['loss'], mvp.get('draw', 0))}{note}）"
                    )
            else:
                career = f"。{_career_label(mvp['nth_season'])}" if mvp.get("nth_season") else ""
                lines.append(f"・今季MVP（{mvp['league']}リーグで最多勝）：{mvp['name']}（性別：{_gender_label(mvp['gender'])}。{_record(mvp['win'], mvp['loss'], mvp.get('draw', 0))}{career}）")
        if rookie:
            rookie_head = (
                f"・新人王：{rookie['name']}（性別：{_gender_label(rookie['gender'])}。"
                + (f"シーズン開始時点で{rookie['age']}歳。" if rookie.get("age") is not None else "")
                + (f"{_career_label(rookie['nth_season'])}。" if rookie.get("nth_season") else "")
                + f"{rookie['league']}リーグ、{_record(rookie['win'], rookie['loss'], rookie.get('draw', 0))}"
            )
            if rookie["title"] and rookie["title_result"] == "初代襲名":
                lines.append(f"{rookie_head}。今季{rookie['title']}の初代保持者に襲名）")
            elif rookie["title"]:
                # title_resultはタイトル側から見た結果（奪取/防衛）。新人王は挑戦者なので、
                # 「防衛」は「挑戦して敗れた」の意味になる（そのまま渡すと逆の意味に読まれる）
                outcome = "奪取" if rookie["title_result"] == "奪取" else "敗退（在位者が防衛）"
                lines.append(f"{rookie_head}。今季{rookie['title']}に挑戦者として出場し{outcome}）")
            else:
                lines.append(f"{rookie_head}）")

    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、第{facts['season']}季のダイジェスト記事を書いてください。"
        "データに無い出来事・数字は書かないこと。"
        "今季MVP・新人王がいる場合は、本文の中でひと言触れること（見出しにする必要はない）。"
        "タイトル戦に師弟対決・兄弟弟子対決・同門対決とあるものは、その関係にも触れて物語性を持たせること"
        "（データに無い師弟・同門関係は書かないこと）。"
        "リーグ戦については、Aリーグに関わる昇格・降格だけ名前を挙げて触れればよい。"
        "B〜Dリーグ内の入れ替えは、件数だけ一言触れる程度で十分（個別の名前は渡していないので書けない）。"
        f"{TITLE_RULES_NOTE}"
        f"{CAREER_PROMPT_NOTE}"
        f"{GENDER_PROMPT_NOTE}"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（300〜500字程度）", '
        '"tags": ["結果", "関係するタイトル名..."]}'
    )
    return "\n".join(lines)


MIN_COLUMN_STREAK = 4  # これ未満の連勝では「所感記事」のネタとして採用しない


def compute_season_win_streaks(matches):
    """指定シーズンの対局ログ（A/B/C/Dリーグのみ）から、個体ごとの最長連勝を集計する。
    対局ログはラウンド単位で記録順に並んでいるため、記録順をそのまま連勝の判定に使う"""
    streak = {}
    best = {}
    for m in matches:
        if m.get("league") not in ("A", "B", "C", "D"):
            continue
        a_id, b_id, result = m["individual_a_id"], m["individual_b_id"], m["result"]
        for iid, outcome in ((a_id, result), (b_id, "loss" if result == "win" else ("win" if result == "loss" else "draw"))):
            if outcome == "win":
                streak[iid] = streak.get(iid, 0) + 1
                best[iid] = max(best.get(iid, 0), streak[iid])
            else:
                streak[iid] = 0
    return best


def gather_column_facts(data_dir, season):
    """「記者の所感記事」用のネタを1つ選ぶ。今のところ「今季の最長連勝」のみを対象とし、
    MIN_COLUMN_STREAK未満なら特筆するネタ無しとしてNoneを返す（記事を作らない）"""
    standings = load_json(os.path.join(data_dir, "standings", f"season_{season}.json"), [])
    matches = load_json(os.path.join(data_dir, "matches", f"season_{season}.json"), [])
    if not standings or not matches:
        return None

    streaks = compute_season_win_streaks(matches)
    if not streaks:
        return None
    top_id, top_streak = max(streaks.items(), key=lambda kv: kv[1])
    if top_streak < MIN_COLUMN_STREAK:
        return None

    row = next((r for r in standings if r["individual_id"] == top_id), None)
    if row is None:
        return None

    # 初在籍がDリーグで、今季はDリーグ以外にいれば「Dリーグからの成り上がり」の物語として扱える。
    # （以前はIDの先頭が「D」かどうかで判定していたが、新人リーグ経由の参入者はID「CC◯-◯◯◯」で
    # 同じくDリーグから始まるため漏れていた。standingsの履歴から判定する）
    prior_seasons, first_by_id = _career_before(data_dir, season)
    first_season, first_league = first_by_id.get(top_id, (season, row["league"]))
    is_grassroots = first_league == "D" and row["league"] != "D"

    return {
        "season": season, "individual_id": top_id, "name": row["display_name"],
        "gender": _load_gender_map(data_dir).get(top_id),
        "league": row["league"], "streak": top_streak,
        "win": row.get("win", 0), "loss": row.get("loss", 0), "draw": row.get("draw", 0),
        "is_grassroots": is_grassroots, "first_season": first_season,
        "nth_season": prior_seasons.get(top_id, 0) + 1,
        "related_individual_ids": [top_id], "related_individual_names": [row["display_name"]],
    }


def build_column_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{facts['name']}（{facts['league']}リーグ、性別：{_gender_label(facts['gender'])}）が、"
        f"{facts['league']}リーグの今季のリーグ戦で{facts['streak']}連勝した"
        f"（今季の{facts['league']}リーグでの成績は{_record(facts['win'], facts['loss'], facts.get('draw', 0))}）。",
    ]
    if facts.get("nth_season"):
        lines.append(f"・{facts['name']}は{_career_label(facts['nth_season'])}。")
    if facts["is_grassroots"]:
        lines.append(f"・{facts['name']}は第{facts['first_season']}季にDリーグから在籍を始め、今季は{facts['league']}リーグで戦った。")
    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、{facts['name']}の{facts['streak']}連勝を主題にした、"
        "記者個人の所感・コラム記事を書いてください（ダイジェスト記事とは別の、短い読み物）。"
        "データに無い出来事・数字は書かないこと。"
        f"{TITLE_RULES_NOTE}"
        f"{CAREER_PROMPT_NOTE}"
        f"{GENDER_PROMPT_NOTE}"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（200〜350字程度）", '
        '"tags": ["コラム", "関係するリーグ名..."]}'
    )
    return "\n".join(lines)


MIN_UPSET_ELO_GAP = 150  # これ未満のElo差では「波乱」のネタとして採用しない


def gather_upset_facts(data_dir, season):
    """「波乱の一局」のネタを1つ選ぶ。A〜Dリーグの通常の総当たりは対象外とし、
    タイトル戦（青龍・朱雀・白虎・玄武の本戦）とその予選トーナメント
    （白虎予選・玄武予選・朱雀予選・朱雀紅白決定戦等）の対局（引き分けを除く）の
    中から、勝者より敗者の方が季開始時点のEloが高かった（かつその差が
    MIN_UPSET_ELO_GAP以上）組み合わせのうち、最もElo差が大きかった1局を選ぶ。
    （同じリーグ内の総当たりでの勝敗は「番狂わせ」と呼ぶには弱く、挑戦者決定戦の
    ようなトーナメント方式・一発勝負の対局の方が下克上として記事になりやすいため）
    「季開始時点のElo」は前季終了時点のEloをそのまま使う（今季中の対局で変動した
    Eloを使うと、勝った結果そのものでEloが上がった選手を「本来強かった」と
    誤判定してしまい、波乱の度合いを過小評価してしまうため）。
    前季の記録が無い個体（今季デビュー）は、デフォルトの開始Elo（1500.0）とみなす。
    該当する対局が無ければNoneを返す"""
    matches = load_json(os.path.join(data_dir, "matches", f"season_{season}.json"), [])
    standings = load_json(os.path.join(data_dir, "standings", f"season_{season}.json"), [])
    if not matches or not standings:
        return None
    names = {r["individual_id"]: r["display_name"] for r in standings}

    prev_standings = load_json(os.path.join(data_dir, "standings", f"season_{season - 1}.json"), [])
    prev_elo = {r["individual_id"]: r["elo"] for r in prev_standings}

    best = None
    for m in matches:
        if m.get("league") in ("A", "B", "C", "D") or m["result"] == "draw":
            continue
        winner_id = m["individual_a_id"] if m["result"] == "win" else m["individual_b_id"]
        loser_id = m["individual_b_id"] if m["result"] == "win" else m["individual_a_id"]
        gap = prev_elo.get(loser_id, 1500.0) - prev_elo.get(winner_id, 1500.0)
        if gap >= MIN_UPSET_ELO_GAP and (best is None or gap > best["gap"]):
            best = {"winner_id": winner_id, "loser_id": loser_id, "gap": gap, "league": m["league"],
                    "stage": m.get("stage")}

    if best is None:
        return None

    winner_id, loser_id = best["winner_id"], best["loser_id"]
    gender_by_id = _load_gender_map(data_dir)
    return {
        "season": season, "league": best["league"], "stage": best["stage"], "elo_gap": round(best["gap"]),
        "winner_id": winner_id, "winner_name": names.get(winner_id, winner_id),
        "winner_gender": gender_by_id.get(winner_id),
        "loser_id": loser_id, "loser_name": names.get(loser_id, loser_id),
        "loser_gender": gender_by_id.get(loser_id),
        "related_individual_ids": [winner_id, loser_id],
        "related_individual_names": [names.get(winner_id, winner_id), names.get(loser_id, loser_id)],
    }


def _match_stage_label(league, stage=None):
    """対局ログのleague欄（"白虎予選"・"朱雀紅組"等）を記事用の呼び名にする。
    以前は「{league}リーグの対局」とそのまま書かせていたため、トーナメントである
    白虎予選や、紅白リーグの入れ替え戦である朱雀予選が「〜予選リーグ」のように誤って書かれていた"""
    if league in TITLE_NAMES:
        return f"{league}戦の番勝負（タイトル保持者と挑戦者によるタイトル戦本番）"
    if league == "麒麟予選":
        if stage and stage.startswith("prelim"):
            if stage[6] == "0":
                return "麒麟戦の予選（ブロック別トーナメント。2組はシードとして後の回戦から登場）"
            return f"麒麟戦の{stage[6]}組予選（ブロック別トーナメント）"
        if stage and stage.startswith("rep"):
            return "麒麟戦の敗者復活戦"
        if stage and stage.startswith("decision"):
            return "麒麟戦の挑戦者決定戦（本戦優勝者は1勝、敗者復活戦の勝者は2連勝で挑戦権）"
        return "麒麟戦の本戦トーナメント（1対戦は先後入れ替えの2局を1本とする3本先取）"
    if league == "白虎予選":
        return "白虎戦の挑戦者決定トーナメント（Elo上位16名による）"
    if league == "玄武予選":
        if stage == "final":
            return "玄武戦の挑戦者決定リーグ（各予選ブロックの優勝者による総当たり）"
        return "玄武戦の予選ブロック（全選手参加の抽選トーナメント）"
    if league == "朱雀予選":
        return ("朱雀紅白リーグの入れ替え戦（紅白リーグからの陥落者の補充枠を争うトーナメント。"
                "勝っても紅白リーグ入りが決まるだけで、朱雀への挑戦権ではない）")
    if league in ("朱雀紅組-playoff", "朱雀白組-playoff"):
        return f"朱雀紅白リーグ（{league[2:4]}）の同率決定戦（順位を決めるための一局）"
    if league in ("朱雀紅組", "朱雀白組"):
        return f"朱雀紅白リーグ（{league[-2:]}）"
    if league == "朱雀挑戦者決定戦":
        return "朱雀挑戦者決定戦（紅組1位と白組1位による一戦）"
    return f"{league}リーグ"


def build_upset_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{_match_stage_label(facts['league'], facts.get('stage'))}の対局で、{facts['winner_name']}（性別：{_gender_label(facts['winner_gender'])}）が"
        f"{facts['loser_name']}（性別：{_gender_label(facts['loser_gender'])}）に勝利した。"
        f"{facts['loser_name']}は前季終了時点のEloが{facts['winner_name']}より約{facts['elo_gap']}ポイント高く、"
        "実力面では下馬評を覆す結果だった。",
        "",
        f"以上の事実だけをもとに、この一局（波乱の一局）を主題にした、"
        "記者個人の所感・コラム記事を書いてください（ダイジェスト記事とは別の、短い読み物）。"
        "データに無い出来事・数字は書かないこと。"
        f"{TITLE_RULES_NOTE}"
        f"{CAREER_PROMPT_NOTE}"
        f"{GENDER_PROMPT_NOTE}"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（200〜350字程度）", '
        '"tags": ["コラム", "関係するリーグ名..."]}'
    ]
    return "\n".join(lines)


def is_eternal_title(title, total, consec):
    """site/api.phpのis_eternal_title()と同じ基準（Python側に実データ・スキーマの
    複製は無いため、season_state.json（title_history）から都度計算する）"""
    if title == "青龍":
        return total >= 5
    if title == "麒麟":
        return consec >= 5 or total >= 7
    if title == "朱雀":
        return consec >= 5 or total >= 10
    if title == "玄武":
        return consec >= 5 or total >= 8
    return consec >= 5  # 白虎


def _title_holder_name(entry):
    # 第1季の「初代襲名」行はholder_nameを持たずnew_holderのみを持つ
    return entry.get("holder_name") or entry.get("new_holder")


def compute_title_stats_up_to(title_history, title, holder_name, season):
    """指定タイトル・保持者名について、season以前（含む）の保持記録から
    通算保持季数と最大連続保持季数を求める（holder_name一致で集計する簡易版）"""
    seasons_held = sorted(
        e["season"] for e in title_history
        if e.get("title") == title and _title_holder_name(e) == holder_name and e.get("season", 0) <= season
    )
    total = len(seasons_held)
    best_streak = 0
    cur_streak = 0
    prev = None
    for s in seasons_held:
        cur_streak = cur_streak + 1 if prev is not None and s == prev + 1 else 1
        best_streak = max(best_streak, cur_streak)
        prev = s
    return total, best_streak


def _title_holder_history(data_dir, title_history, title, holder_name, season):
    """holder_nameがそのタイトルを保持した各季の経緯（初代襲名／誰から奪取／誰の挑戦を防衛）を
    古い順に文字列で返す。永世称号の特集記事で、実際の経緯に基づいて書かせるため"""
    names = {}
    rosters = load_json(os.path.join(data_dir, "rosters.json"), {})
    for league_members in rosters.values():
        for ind in league_members:
            names[ind["id"]] = ind.get("display_name")
    for r in load_json(os.path.join(data_dir, "season_state.json"), {}).get("retired_archive", []):
        names.setdefault(r["id"], r.get("display_name"))

    lines = []
    for e in sorted((e for e in title_history if e.get("title") == title and e.get("season", 0) <= season),
                    key=lambda e: e["season"]):
        if _title_holder_name(e) != holder_name:
            continue
        if e.get("event") == "初代襲名":
            lines.append(f"第{e['season']}季：在位者不在のため、挑戦者決定を勝ち抜いて番勝負なしで初代襲名")
        elif e.get("won"):
            prev = names.get(e.get("defender_id"), "前保持者")
            lines.append(f"第{e['season']}季：挑戦者決定を勝ち抜いて挑戦者となり、保持者{prev}を"
                         f"{e.get('challenger_wins')}-{e.get('titleholder_wins')}で破って奪取")
        else:
            lines.append(f"第{e['season']}季：在位者として挑戦者決定には出場せず、勝ち上がってきた挑戦者"
                         f"{e.get('challenger_name')}を{e.get('titleholder_wins')}-{e.get('challenger_wins')}で退けて防衛")
    return lines


def gather_milestone_facts(data_dir, season):
    """この季に、誰かが初めて永世称号の基準を新たに満たしたかを検出する
    （既に前季までに達成済みなら対象外）。最初に見つかった1件のみをネタとする"""
    state = load_json(os.path.join(data_dir, "season_state.json"), {})
    title_history = state.get("title_history", [])
    this_season_entries = [e for e in title_history if e.get("season") == season]

    for e in this_season_entries:
        title = e.get("title")
        holder_name = _title_holder_name(e)
        if not title or not holder_name:
            continue
        total_after, streak_after = compute_title_stats_up_to(title_history, title, holder_name, season)
        if not is_eternal_title(title, total_after, streak_after):
            continue
        total_before, streak_before = compute_title_stats_up_to(title_history, title, holder_name, season - 1)
        if is_eternal_title(title, total_before, streak_before):
            continue  # 前季までに既に達成済み（新規達成ではない）

        return {
            "season": season, "title": title, "name": holder_name,
            "total": total_after, "streak": streak_after,
            "history": _title_holder_history(data_dir, title_history, title, holder_name, season),
            "related_individual_ids": [e["holder_id"]] if e.get("holder_id") else [],
            "related_individual_names": [holder_name],
        }
    return None


def build_milestone_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{facts['name']}が、{facts['title']}位で「永世{facts['title']}」の称号基準を満たした"
        f"（通算保持{facts['total']}期、最大連続保持{facts['streak']}期）。",
        "・保持した各季の経緯：",
        *[f"　・{h}" for h in facts.get("history", [])],
        "",
        f"以上の事実だけをもとに、{facts['name']}の永世{facts['title']}達成を主題にした、"
        "特集記事（コラムより少し丁寧な読み物）を書いてください。"
        "データに無い出来事・数字は書かないこと。"
        f"{TITLE_RULES_NOTE}"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（250〜400字程度）", '
        '"tags": ["特集", "永世称号", "関係するタイトル名"]}'
    ]
    return "\n".join(lines)


def variety_note(articles, fmt=None, focus=None):
    """記事が毎季同じ書き方・同じ見出しの型にならないよう、プロンプトの末尾に付ける指示。
    直近の記事の見出しを見せて似た言い回しを避けさせ、書き方の型・切り口を指定する"""
    note = ""
    if fmt:
        note += f"\n書き方の型：{fmt[0]}（{fmt[1]}）。"
    if focus:
        note += f"\n今回の切り口：{focus}。"
    note += ("\nリーグの内部の仕組み（終盤力・定跡・調子・降級点などのルールの定義や数値の意味）は説明しないこと。"
             "ファンが読む選手の物語として書くこと。")
    titles = news_topics.recent_titles(articles)
    if titles:
        note += ("\n直近の記事の見出しは次のとおり。見出し・書き出し・締めの言い回しがこれらと似ないようにすること"
                 "（「○○、N連勝」「約Nポイント差を越えて」のような同じ型の見出しを繰り返さない）：\n"
                 + "\n".join(f"・{t}" for t in titles))
    return note + "\n"


# まとめ記事の切り口（季ごとに持ち回り）
DIGEST_FOCUSES = [
    "タイトル戦の勝者（奪取・防衛した側）を中心に",
    "タイトル戦で敗れた側・惜しくも届かなかった選手にも光を当てて",
    "MVP・新人王など今季の顔ぶれを中心に",
    "タイトルの勢力図（誰が何冠を持っているか）の変化を中心に",
]


def run_claude(prompt, model=DEFAULT_MODEL, persona_path=PERSONA_PATH):
    with open(persona_path, "r", encoding="utf-8") as f:
        persona = f.read()

    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--system-prompt", persona,
            "--model", model,
            "--output-format", "json",
            "--tools", "",
        ],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLIの実行に失敗しました（終了コード{result.returncode}）: {result.stderr.strip()}"
        )

    response = json.loads(result.stdout)
    if response.get("is_error"):
        raise RuntimeError(f"claude CLIがエラーを返しました: {response.get('result')}")

    text = response["result"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def generate_article(facts, model=DEFAULT_MODEL, articles=None):
    focus = DIGEST_FOCUSES[facts.get("season", 0) % len(DIGEST_FOCUSES)]
    return run_claude(build_prompt(facts) + variety_note(articles or [], focus=focus), model=model)


def save_article(articles_path, articles, article):
    articles.append(article)
    os.makedirs(os.path.dirname(articles_path), exist_ok=True)
    with open(articles_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] {articles_path} に記事を追加しました（id={article['id']}）", flush=True)


def try_generate_digest(args, season, articles_path, articles):
    article_id = f"s{season}-digest"
    existing = next((a for a in articles if a.get("id") == article_id), None)
    if existing is not None and not getattr(args, "regenerate_digest", False):
        print(f"[DEBUG] 第{season}季のダイジェストは既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    facts = gather_season_facts(args.data_dir, season)
    if not facts["title_results"]:
        print(f"[DEBUG] 第{season}季のタイトル戦データが見つかりません。ダイジェストをスキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季のダイジェストを生成します（model={args.model}）", flush=True)
    generated = generate_article(facts, model=args.model, articles=articles)

    if existing is not None:
        # 作り直し：同じidの記事を置き換える（いいね等はidに紐づくため引き継がれる。
        # 掲載日時も元の記事のものを引き継ぎ、一覧での並び順を変えない）
        print(f"[DEBUG] 第{season}季のダイジェストを作り直しました（{article_id}）", flush=True)
        articles.remove(existing)
    save_article(articles_path, articles, {
        "id": article_id, "type": "result", "season": season, "author": AUTHOR_DIGEST,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": (existing or {}).get("published_at")
        or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["結果"],
        "generated_by": args.model,
    })


COLUMNS_PER_SEASON = 2


def _column_candidates(data_dir, season):
    """コラムの候補（news_topics.pyの各テーマ＋従来の連勝・波乱の一局）を、話題の大きさ付きで集める"""
    cands = []
    streak = gather_column_facts(data_dir, season)
    if streak:
        cands.append(dict(topic="連勝", score=4 * streak["streak"], prompt=lambda f=streak: build_column_prompt(f),
                          facts=streak, label=f"{streak['name']}の{streak['streak']}連勝", tags=["コラム"]))
    upset = gather_upset_facts(data_dir, season)
    if upset:
        cands.append(dict(topic="波乱", score=10 + upset["elo_gap"] // 10, prompt=lambda f=upset: build_upset_prompt(f),
                          facts=upset, label=f"{upset['winner_name']}が{upset['loser_name']}に勝った一局", tags=["コラム"]))
    ctx = news_topics.build_context(data_dir, season, _match_stage_label)
    for fn in news_topics.TOPIC_FUNCS:
        t = fn(ctx)
        if t:
            cands.append(dict(topic=t["topic"], score=t["score"], prompt=lambda t=t, season=season: build_topic_prompt(season, t),
                              facts=t, label=t["subject"], tags=t["tags"]))
    return cands


def build_topic_prompt(season, t):
    lines = [f"第{season}季の事実データ（これ以外の出来事は起きていない）:", ""]
    lines += [f"・{x}" for x in t["lines"]]
    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、{t['subject']}を主題にした、記者個人のコラム記事を書いてください"
        "（ダイジェスト記事とは別の、短い読み物）。データに無い出来事・数字・発言は書かないこと。"
        f"{TITLE_RULES_NOTE}"
        f"{CAREER_PROMPT_NOTE}"
        f"{GENDER_PROMPT_NOTE}"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（200〜350字程度）", '
        '"tags": ["コラム", "関係するリーグ名・タイトル名..."]}'
    )
    return "\n".join(lines)


def try_generate_columns(args, season, articles_path, articles):
    """季ごとのコラム（最大COLUMNS_PER_SEASON本）。テーマは話題の大きさで選び、直近の季に使ったテーマは
    点数を下げて避ける。書き方の型も持ち回りにする（以前は毎季「連勝」「波乱の一局」で固定だった）"""
    regenerate = getattr(args, "regenerate_column", False) or getattr(args, "regenerate_upset", False)
    ids = [f"s{season}-column", f"s{season}-column2"]
    existing = [a for a in articles if a.get("id") in ids or a.get("id") == f"s{season}-upset"]
    if existing and not regenerate:
        print(f"[DEBUG] 第{season}季のコラムは既に生成済みです。スキップします", flush=True)
        return
    cands = _column_candidates(args.data_dir, season)
    used = news_topics.recent_topics([a for a in articles if a not in existing], season)
    for c in cands:
        c["adj"] = c["score"] - (news_topics.RECENT_TOPIC_PENALTY if c["topic"] in used else 0)
    cands.sort(key=lambda c: -c["adj"])
    chosen, seen = [], set()
    for c in cands:
        if c["topic"] in seen:
            continue
        chosen.append(c); seen.add(c["topic"])
        if len(chosen) == COLUMNS_PER_SEASON:
            break
    print("[DEBUG] コラム候補：" + "、".join(f"{c['topic']}（{c['adj']}点：{c['label']}）" for c in cands), flush=True)
    if not chosen:
        print(f"[DEBUG] 第{season}季はコラムにするネタが見つかりません。スキップします", flush=True)
        return
    old_by_id = {a["id"]: a for a in existing}
    for a in existing:
        articles.remove(a)
    for k, (c, article_id) in enumerate(zip(chosen, ids)):
        fmt = news_topics.pick_format(articles, season, k)
        print(f"[DEBUG] 第{season}季のコラム{k + 1}を生成します（テーマ：{c['topic']}、{c['label']}、型：{fmt[0]}、model={args.model}）", flush=True)
        generated = run_claude(c["prompt"]() + variety_note(articles, fmt=fmt), model=args.model, persona_path=COLUMN_PERSONA_PATH)
        prev = old_by_id.get(article_id) or (old_by_id.get(f"s{season}-upset") if k == 1 else None)
        save_article(articles_path, articles, {
            "id": article_id, "type": "column", "season": season, "author": AUTHOR_COLUMN, "topic": c["topic"],
            "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
            "published_at": (prev or {}).get("published_at")
            or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "related_individual_ids": c["facts"]["related_individual_ids"],
            "related_individual_names": c["facts"]["related_individual_names"],
            "tags": generated.get("tags") or c["tags"],
            "generated_by": args.model,
        })


def try_generate_milestone(args, season, articles_path, articles):
    facts = gather_milestone_facts(args.data_dir, season)
    if facts is None:
        print(f"[DEBUG] 第{season}季は永世称号の新規達成が見つかりません。特集をスキップします", flush=True)
        return

    article_id = f"s{season}-{facts['title']}-eternal"
    existing = next((a for a in articles if a.get("id") == article_id), None)
    if existing is not None and not getattr(args, "regenerate_milestone", False):
        print(f"[DEBUG] 第{season}季の永世{facts['title']}特集は既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季の特集を生成します（{facts['name']}が永世{facts['title']}達成、model={args.model}）", flush=True)
    generated = run_claude(build_milestone_prompt(facts), model=args.model, persona_path=COLUMN_PERSONA_PATH)

    if existing is not None:
        # 作り直し：同じid・同じ掲載日時のまま置き換える（いいね等はidに紐づくため引き継がれる）
        articles.remove(existing)
    save_article(articles_path, articles, {
        "id": article_id, "type": "feature", "season": season, "author": AUTHOR_COLUMN,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": (existing or {}).get("published_at")
        or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["特集", "永世称号"],
        "generated_by": args.model,
    })


def gather_newcomer_facts(data_dir, season):
    """
    その季からDリーグへ出走する新人リーグ結果（data/newcomer_league/for_season_{season}.json）
    から、記事生成に使う構造化データを集める。ファイルが無い（新人リーグ未実施・
    応募ゼロ等）場合はNoneを返す。
    """
    nl = load_json(os.path.join(data_dir, "newcomer_league", f"for_season_{season}.json"), None)
    if not nl or not nl.get("standings"):
        return None

    # 昇格者の実IDは "CC{for_season}-{rank-1:03d}" になる（run_newcomer_league.pyの
    # winner_entries→league.py:_build_character_creation_individualの命名規則と同じ）。
    # rosters.jsonに同名の個体が実在することを確認してから使う（site/api.phpの
    # resolve_newcomer_real_idsと同じ安全策）
    rosters = load_json(os.path.join(data_dir, "rosters.json"), {})
    by_id = {ind["id"]: ind for league in ("A", "B", "C", "D") for ind in rosters.get(league, [])}

    promoted = []
    related = []
    for row in nl["standings"]:
        if not row.get("promoted"):
            continue
        real_id = f"CC{season}-{row['rank'] - 1:03d}"
        real_ind = by_id.get(real_id)
        resolved_id = real_id if (real_ind and real_ind.get("display_name") == row["display_name"]) else None
        promoted.append({
            "name": row["display_name"], "win": row["win"], "loss": row["loss"], "draw": row.get("draw", 0),
            "auto_generated": bool(row.get("auto_generated")), "master_name": row.get("master_name"),
        })
        if resolved_id:
            related.append((resolved_id, row["display_name"]))

    return {
        "season": season,
        "submission_count": nl.get("submission_count", 0),
        "slots_needed": nl.get("slots_needed", len(promoted)),
        "participant_count": len(nl["standings"]),
        "promoted": promoted,
        "had_playoff": bool(nl.get("promotion_playoff")),
        "related_individual_ids": [iid for iid, _ in related],
        "related_individual_names": [name for _, name in related],
    }


def build_newcomer_prompt(facts):
    lines = [
        f"第{facts['season']}季から出走する新人リーグ結果データ（事実のみ。これ以外の出来事は起きていない）:", "",
        # 読者投稿の受付を止めている間（投稿0件）は、投稿・自動生成の区別自体を渡さない
        # （渡すと「読者からの投稿はなく全員が自動生成」のような、受付停止中には不自然な一文が入る）
        f"・参加者数：{facts['participant_count']}名"
        + (f"（うち読者からの投稿：{facts['submission_count']}名、残りは自動生成）" if facts["submission_count"] else ""),
        f"・Dリーグ昇格枠：{facts['slots_needed']}名",
    ]
    for p in facts["promoted"]:
        master_part = f"（師匠：{p['master_name']}）" if p.get("master_name") else "（師匠なし）"
        origin = ("　読者投稿" if not p["auto_generated"] else "　自動生成") if facts["submission_count"] else ""
        lines.append(f"・昇格：{p['name']}{master_part}　{p['win']}勝{p['loss']}敗{p['draw']}分{origin}")
    if facts["had_playoff"]:
        lines.append("・昇格枠の境界で同成績が並び、プレーオフで昇格者を決定した。")
    lines.append("")
    lines.append(
        "以上の事実だけをもとに、今回の新人リーグ結果を紹介する短い記事を書いてください。"
        "新たにDリーグへ参入する顔ぶれを紹介する、記者（犬飼）の速報記事という位置づけです。"
        "データに無い出来事・数字は書かないこと。"
        + ("" if facts["submission_count"] else "読者からの投稿や、自動生成かどうかには触れないこと。")
        + "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（200〜350字程度）", '
        '"tags": ["新人リーグ", "結果"]}'
    )
    return "\n".join(lines)


def try_generate_newcomer(args, season, articles_path, articles):
    article_id = f"s{season}-newcomer"
    existing = next((a for a in articles if a.get("id") == article_id), None)
    if existing is not None and not getattr(args, "regenerate_newcomer", False):
        # 新人リーグ直後に生成した記事は、昇格者がまだDリーグに参入していないため関連個体
        # （選手へのリンク）が空になる。参入後の実行で、本文は変えずに関連個体だけ補う
        if not existing.get("related_individual_ids"):
            facts = gather_newcomer_facts(args.data_dir, season)
            if facts and facts["related_individual_ids"]:
                existing["related_individual_ids"] = facts["related_individual_ids"]
                existing["related_individual_names"] = facts["related_individual_names"]
                with open(articles_path, "w", encoding="utf-8") as f:
                    json.dump(articles, f, ensure_ascii=False, indent=2)
                print(f"[DEBUG] {article_id}に関連個体を追加しました", flush=True)
                return
        print(f"[DEBUG] 第{season}季の新人リーグ記事は既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    facts = gather_newcomer_facts(args.data_dir, season)
    if facts is None:
        print(f"[DEBUG] 第{season}季の新人リーグ結果データが見つかりません。記事生成をスキップします", flush=True)
        return
    if not facts["promoted"]:
        print(f"[DEBUG] 第{season}季は新人リーグの昇格者がいません。記事生成をスキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季の新人リーグ記事を生成します（model={args.model}）", flush=True)
    generated = run_claude(build_newcomer_prompt(facts) + variety_note(articles), model=args.model, persona_path=PERSONA_PATH)

    # 新人リーグは、新人が出走する季の本戦より前に行われる。保険として本戦の後に生成した場合も、
    # 一覧で本戦記事より前に並ぶよう、その季のダイジェストより前の日時にする
    published_at = datetime.datetime.now().astimezone()
    digest = next((a for a in articles if a.get("id") == f"s{season}-digest"), None)
    if existing is not None:
        # 作り直し：同じid・同じ掲載日時のまま置き換える
        articles.remove(existing)
        published_at = datetime.datetime.fromisoformat(existing["published_at"])
    elif digest:
        digest_at = datetime.datetime.fromisoformat(digest["published_at"])
        if published_at >= digest_at:
            published_at = digest_at - datetime.timedelta(minutes=1)
    save_article(articles_path, articles, {
        "id": article_id, "type": "result", "season": season, "author": AUTHOR_DIGEST,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": published_at.isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["新人リーグ", "結果"],
        "generated_by": args.model,
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--news-dir", default="data/news")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--season", type=int, default=None, help="対象シーズン（省略時は最新）")
    parser.add_argument("--regenerate-digest", action="store_true",
                        help="対象季のダイジェスト記事が既にあっても作り直す（記事の誤りを修正する場合）")
    parser.add_argument("--regenerate-milestone", action="store_true",
                        help="対象季の永世称号の特集記事が既にあっても作り直す（記事の誤りを修正する場合）")
    parser.add_argument("--regenerate-newcomer", action="store_true",
                        help="対象季の新人リーグ記事が既にあっても作り直す（掲載日時は元のまま）")
    parser.add_argument("--regenerate-column", action="store_true",
                        help="対象季のコラム（2本）が既にあっても作り直す（記事の誤りを修正する場合。掲載日時は元のまま）")
    parser.add_argument("--regenerate-upset", action="store_true",
                        help="--regenerate-columnと同じ（旧「波乱の一局」コラム用の指定。今はコラム2本をまとめて作り直す）")
    parser.add_argument("--newcomer-only", action="store_true",
                        help="対象季（＝新人が出走する季）の新人リーグ記事だけを生成する。新人リーグ（newcomer_league.yml）の"
                             "直後に、その回の記事をすぐ出すために使う")
    args = parser.parse_args()

    season = args.season or latest_season_with_standings(args.data_dir)
    if season is None:
        print("[DEBUG] 対象シーズンのデータが見つかりません", flush=True)
        return

    articles_path = os.path.join(args.news_dir, "articles.json")
    articles = load_json(articles_path, [])

    if args.newcomer_only:
        try_generate_newcomer(args, season, articles_path, articles)
        return

    try_generate_digest(args, season, articles_path, articles)
    # 新人リーグ記事は通常、新人リーグ直後の--newcomer-only実行で生成済み。その実行が
    # 失敗していた場合の保険としてここでも試みる（既に生成済みなら何もしない）
    try_generate_newcomer(args, season, articles_path, articles)
    try_generate_columns(args, season, articles_path, articles)
    try_generate_milestone(args, season, articles_path, articles)


if __name__ == "__main__":
    main()
