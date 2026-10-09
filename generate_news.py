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

DEFAULT_MODEL = "sonnet"
TITLE_NAMES = ["青龍", "朱雀", "白虎", "玄武"]
# タイトルの格（青龍＝名人格が最高、玄武が最も格下）。MVP・新人王の評価で
# タイトル絡みの実績を比較する際の同格内タイブレークに使う（site/api.phpのDAN_*定義と同じ順）
TITLE_PRESTIGE = {name: len(TITLE_NAMES) - i for i, name in enumerate(TITLE_NAMES)}
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

    achievers = {}  # individual_id -> {"name", "score", "titles": [(title, result), ...]}
    for tr in title_results:
        wid, wname = tr["winner_id"], tr["winner_name"]
        entry = achievers.setdefault(wid, {"name": wname, "score": 0, "titles": []})
        entry["score"] += TITLE_PRESTIGE.get(tr["title"], 0)
        entry["titles"].append((tr["title"], tr["result"]))

    if achievers:
        best_id = max(achievers, key=lambda iid: (achievers[iid]["score"], win_by_id.get(iid, 0), iid))
        chosen = achievers[best_id]
        return {
            "individual_id": best_id, "name": chosen["name"], "titles": chosen["titles"],
            "league": next((r["league"] for r in main_league_rows if r["individual_id"] == best_id), None),
            "win": win_by_id.get(best_id, 0), "loss": loss_by_id.get(best_id, 0),
        }

    fallback = _pick_best_record(main_league_rows)
    if fallback is None:
        return None
    return {
        "individual_id": fallback["individual_id"], "name": fallback["display_name"], "titles": [],
        "league": fallback["league"], "win": fallback.get("win", 0), "loss": fallback.get("loss", 0),
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
    dan_win_step = {5: 30, 6: 40, 7: 50, 8: 70, 9: 100}
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


def _pick_rookie_of_year(data_dir, season, rosters, main_league_rows, title_results):
    """新人王を選ぶ。対象は「今季デビューの新規参入者」ではなく（Dリーグ新人受け入れが
    毎季固定2名のため、そこだけに絞ると実質二択になってしまう）、シーズン開始時点で
    ①26歳以下 ②六段以下 ③タイトル戦経験が無い、という条件を満たす個体全員。
    その中から、今季タイトルを奪取・挑戦した個体を最優先し（新人の年齢・段位で
    タイトル戦に絡めば十分な実績と言えるため）、それが無ければ勝率→勝数で最も
    活躍した個体を選ぶ。該当者がいなければNoneを返す"""
    age_by_id = {
        ind["id"]: ind.get("initial_age", 0) + ind.get("total_seasons", 0)
        for league_members in rosters.values() for ind in league_members
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
        score = (ROOKIE_TITLE_CAPTURE_SCORE if tr["result"] == "奪取" else ROOKIE_TITLE_CHALLENGE_SCORE) \
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
        "title": tr["title"] if tr else None, "title_result": tr["result"] if tr else None,
    }


def gather_season_facts(data_dir, season):
    """指定シーズンの対局結果から、記事生成に使う構造化データ（事実）を集める。
    タイトル戦の勝者側（奪取なら挑戦者、防衛ならホルダー）のIDを
    related_individual_idsとしてまとめて返す（記事の「関連個体」表示用）"""
    standings = load_json(os.path.join(data_dir, "standings", f"season_{season}.json"), [])
    matches = load_json(os.path.join(data_dir, "matches", f"season_{season}.json"), [])
    rosters = load_json(os.path.join(data_dir, "rosters.json"), {})
    names = {r["individual_id"]: r["display_name"] for r in standings}

    main_league_rows = [r for r in standings if r.get("league") in ("A", "B", "C", "D")]

    title_results = []
    related = []  # [(id, name), ...]（勝者のみ、重複は後で除去）
    for t in TITLE_NAMES:
        rows = [m for m in matches if m.get("league") == t]
        if not rows:
            continue
        a_id, b_id = rows[0]["individual_a_id"], rows[0]["individual_b_id"]
        win_a = sum(1 for r in rows if r["result"] == "win")
        loss_a = sum(1 for r in rows if r["result"] == "loss")
        challenger_won = win_a > loss_a
        winner_id = a_id if challenger_won else b_id
        title_results.append({
            "title": t, "challenger_id": a_id, "holder_id": b_id,
            "winner_id": winner_id, "winner_name": names.get(winner_id, winner_id),
            "challenger_name": names.get(a_id, a_id), "holder_name": names.get(b_id, b_id),
            "challenger_wins": win_a, "holder_wins": loss_a,
            "result": "奪取" if challenger_won else "防衛",
        })
        related.append((winner_id, names.get(winner_id, winner_id)))

    mvp = _pick_mvp(title_results, main_league_rows)
    if mvp is not None:
        related.append((mvp["individual_id"], mvp["name"]))

    rookie = _pick_rookie_of_year(data_dir, season, rosters, main_league_rows, title_results)
    # MVPと同一人物であれば、新人王は重複して取り上げない
    if rookie is not None and (mvp is None or rookie["individual_id"] != mvp["individual_id"]):
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


def build_prompt(facts):
    lines = [f"第{facts['season']}季の結果データ（事実のみ。これ以外の出来事は起きていない）:", "", "■タイトル戦"]
    for tr in facts["title_results"]:
        lines.append(
            f"・{tr['title']}：挑戦者{tr['challenger_name']} {tr['challenger_wins']}-{tr['holder_wins']} "
            f"保持者{tr['holder_name']} → {tr['result']}"
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
    if mv["new"]:
        lines.append(f"・新規参入：{'、'.join(mv['new'])}")
    if mv["retired"]:
        lines.append(f"・引退：{'、'.join(mv['retired'])}")

    mvp, rookie = facts.get("mvp"), facts.get("rookie")
    if mvp or rookie:
        lines.append("")
        lines.append("■今季の活躍")
        if mvp:
            if mvp["titles"]:
                titles_text = "・".join(f"{t}{r}" for t, r in mvp["titles"])
                lines.append(
                    f"・今季MVP：{mvp['name']}（{titles_text}。{mvp['league']}リーグ{mvp['win']}勝{mvp['loss']}敗）"
                )
            else:
                lines.append(f"・今季MVP（{mvp['league']}リーグで最多勝）：{mvp['name']}（{mvp['win']}勝{mvp['loss']}敗）")
        if rookie:
            if rookie["title"]:
                lines.append(
                    f"・新人王：{rookie['name']}（{rookie['league']}リーグ、{rookie['win']}勝{rookie['loss']}敗。"
                    f"今季{rookie['title']}に挑戦し{rookie['title_result']}）"
                )
            else:
                lines.append(f"・新人王：{rookie['name']}（{rookie['league']}リーグ、{rookie['win']}勝{rookie['loss']}敗）")

    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、第{facts['season']}季のダイジェスト記事を書いてください。"
        "データに無い出来事・数字は書かないこと。"
        "今季MVP・新人王がいる場合は、本文の中でひと言触れること（見出しにする必要はない）。"
        "リーグ戦については、Aリーグに関わる昇格・降格だけ名前を挙げて触れればよい。"
        "B〜Dリーグ内の入れ替えは、件数だけ一言触れる程度で十分（個別の名前は渡していないので書けない）。"
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

    # IDの先頭が「D」＝Dリーグの新人として参入した個体（命名規則上の由来）。
    # 現在Dリーグ以外にいれば「Dリーグ出身からの成り上がり」の物語として扱える
    is_grassroots = top_id.startswith("D") and row["league"] != "D"

    return {
        "season": season, "individual_id": top_id, "name": row["display_name"],
        "league": row["league"], "streak": top_streak,
        "win": row.get("win", 0), "loss": row.get("loss", 0),
        "is_grassroots": is_grassroots,
        "related_individual_ids": [top_id], "related_individual_names": [row["display_name"]],
    }


def build_column_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{facts['name']}（{facts['league']}リーグ）が、リーグ戦（A/B/C/Dいずれか）で{facts['streak']}連勝した"
        f"（今季の成績は{facts['win']}勝{facts['loss']}敗）。",
    ]
    if facts["is_grassroots"]:
        lines.append(f"・{facts['name']}はDリーグの新人としてデビューし、現在は{facts['league']}リーグまで昇格している。")
    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、{facts['name']}の{facts['streak']}連勝を主題にした、"
        "記者個人の所感・コラム記事を書いてください（ダイジェスト記事とは別の、短い読み物）。"
        "データに無い出来事・数字は書かないこと。"
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
            best = {"winner_id": winner_id, "loser_id": loser_id, "gap": gap, "league": m["league"]}

    if best is None:
        return None

    winner_id, loser_id = best["winner_id"], best["loser_id"]
    return {
        "season": season, "league": best["league"], "elo_gap": round(best["gap"]),
        "winner_id": winner_id, "winner_name": names.get(winner_id, winner_id),
        "loser_id": loser_id, "loser_name": names.get(loser_id, loser_id),
        "related_individual_ids": [winner_id, loser_id],
        "related_individual_names": [names.get(winner_id, winner_id), names.get(loser_id, loser_id)],
    }


def build_upset_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{facts['league']}リーグの対局で、{facts['winner_name']}が{facts['loser_name']}に勝利した。"
        f"{facts['loser_name']}は前季終了時点のEloが{facts['winner_name']}より約{facts['elo_gap']}ポイント高く、"
        "実力面では下馬評を覆す結果だった。",
        "",
        f"以上の事実だけをもとに、この一局（波乱の一局）を主題にした、"
        "記者個人の所感・コラム記事を書いてください（ダイジェスト記事とは別の、短い読み物）。"
        "データに無い出来事・数字は書かないこと。"
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
    if title == "朱雀":
        return consec >= 5 or total >= 10
    if title == "玄武":
        return total >= 10
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
            "related_individual_ids": [e["holder_id"]] if e.get("holder_id") else [],
            "related_individual_names": [holder_name],
        }
    return None


def build_milestone_prompt(facts):
    lines = [
        f"第{facts['season']}季の事実データ（これ以外の出来事は起きていない）:", "",
        f"・{facts['name']}が、{facts['title']}位で「永世{facts['title']}」の称号基準を満たした"
        f"（通算保持{facts['total']}期、最大連続保持{facts['streak']}期）。",
        "",
        f"以上の事実だけをもとに、{facts['name']}の永世{facts['title']}達成を主題にした、"
        "特集記事（コラムより少し丁寧な読み物）を書いてください。"
        "データに無い出来事・数字は書かないこと。"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（250〜400字程度）", '
        '"tags": ["特集", "永世称号", "関係するタイトル名"]}'
    ]
    return "\n".join(lines)


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


def generate_article(facts, model=DEFAULT_MODEL):
    return run_claude(build_prompt(facts), model=model)


def save_article(articles_path, articles, article):
    articles.append(article)
    os.makedirs(os.path.dirname(articles_path), exist_ok=True)
    with open(articles_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] {articles_path} に記事を追加しました（id={article['id']}）", flush=True)


def try_generate_digest(args, season, articles_path, articles):
    article_id = f"s{season}-digest"
    if any(a.get("id") == article_id for a in articles):
        print(f"[DEBUG] 第{season}季のダイジェストは既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    facts = gather_season_facts(args.data_dir, season)
    if not facts["title_results"]:
        print(f"[DEBUG] 第{season}季のタイトル戦データが見つかりません。ダイジェストをスキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季のダイジェストを生成します（model={args.model}）", flush=True)
    generated = generate_article(facts, model=args.model)

    save_article(articles_path, articles, {
        "id": article_id, "type": "result", "season": season, "author": AUTHOR_DIGEST,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["結果"],
        "generated_by": args.model,
    })


def try_generate_column(args, season, articles_path, articles):
    article_id = f"s{season}-column"
    if any(a.get("id") == article_id for a in articles):
        print(f"[DEBUG] 第{season}季のコラムは既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    facts = gather_column_facts(args.data_dir, season)
    if facts is None:
        print(f"[DEBUG] 第{season}季はコラムにするネタ（{MIN_COLUMN_STREAK}連勝以上）が見つかりません。スキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季のコラムを生成します（{facts['name']}の{facts['streak']}連勝、model={args.model}）", flush=True)
    generated = run_claude(build_column_prompt(facts), model=args.model, persona_path=COLUMN_PERSONA_PATH)

    save_article(articles_path, articles, {
        "id": article_id, "type": "column", "season": season, "author": AUTHOR_COLUMN,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["コラム"],
        "generated_by": args.model,
    })


def try_generate_upset(args, season, articles_path, articles):
    article_id = f"s{season}-upset"
    if any(a.get("id") == article_id for a in articles):
        print(f"[DEBUG] 第{season}季の波乱の一局コラムは既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    facts = gather_upset_facts(args.data_dir, season)
    if facts is None:
        print(f"[DEBUG] 第{season}季は波乱の一局にするネタ（Elo差{MIN_UPSET_ELO_GAP}以上での下克上）が見つかりません。スキップします", flush=True)
        return

    print(
        f"[DEBUG] 第{season}季の波乱の一局コラムを生成します"
        f"（{facts['winner_name']}が{facts['loser_name']}に勝利、Elo差約{facts['elo_gap']}、model={args.model}）",
        flush=True,
    )
    generated = run_claude(build_upset_prompt(facts), model=args.model, persona_path=COLUMN_PERSONA_PATH)

    save_article(articles_path, articles, {
        "id": article_id, "type": "column", "season": season, "author": AUTHOR_COLUMN,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "related_individual_ids": facts["related_individual_ids"],
        "related_individual_names": facts["related_individual_names"],
        "tags": generated.get("tags") or ["コラム"],
        "generated_by": args.model,
    })


def try_generate_milestone(args, season, articles_path, articles):
    facts = gather_milestone_facts(args.data_dir, season)
    if facts is None:
        print(f"[DEBUG] 第{season}季は永世称号の新規達成が見つかりません。特集をスキップします", flush=True)
        return

    article_id = f"s{season}-{facts['title']}-eternal"
    if any(a.get("id") == article_id for a in articles):
        print(f"[DEBUG] 第{season}季の永世{facts['title']}特集は既に生成済みです（{article_id}）。スキップします", flush=True)
        return

    print(f"[DEBUG] 第{season}季の特集を生成します（{facts['name']}が永世{facts['title']}達成、model={args.model}）", flush=True)
    generated = run_claude(build_milestone_prompt(facts), model=args.model, persona_path=COLUMN_PERSONA_PATH)

    save_article(articles_path, articles, {
        "id": article_id, "type": "feature", "season": season, "author": AUTHOR_COLUMN,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
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
        f"・参加者数：{facts['participant_count']}名（うち読者からの投稿：{facts['submission_count']}名、残りは自動生成）",
        f"・Dリーグ昇格枠：{facts['slots_needed']}名",
    ]
    for p in facts["promoted"]:
        master_part = f"（師匠：{p['master_name']}）" if p.get("master_name") else "（新規開祖）"
        origin = "読者投稿" if not p["auto_generated"] else "自動生成"
        lines.append(f"・昇格：{p['name']}{master_part}　{p['win']}勝{p['loss']}敗{p['draw']}分　{origin}")
    if facts["had_playoff"]:
        lines.append("・昇格枠の境界で同成績が並び、プレーオフで昇格者を決定した。")
    lines.append("")
    lines.append(
        "以上の事実だけをもとに、今回の新人リーグ結果を紹介する短い記事を書いてください。"
        "新たにDリーグへ参入する顔ぶれを紹介する、記者（犬飼）の速報記事という位置づけです。"
        "データに無い出来事・数字は書かないこと。"
        "出力は次のJSON形式のみ（説明文やコードフェンスなど、他のテキストは一切含めない）：\n"
        '{"title": "見出し", "summary": "1〜2文の要約", "body": "本文（200〜350字程度）", '
        '"tags": ["新人リーグ", "結果"]}'
    )
    return "\n".join(lines)


def try_generate_newcomer(args, season, articles_path, articles):
    article_id = f"s{season}-newcomer"
    if any(a.get("id") == article_id for a in articles):
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
    generated = run_claude(build_newcomer_prompt(facts), model=args.model, persona_path=PERSONA_PATH)

    save_article(articles_path, articles, {
        "id": article_id, "type": "result", "season": season, "author": AUTHOR_DIGEST,
        "title": generated["title"], "summary": generated["summary"], "body": generated["body"],
        "published_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
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
    args = parser.parse_args()

    season = args.season or latest_season_with_standings(args.data_dir)
    if season is None:
        print("[DEBUG] 対象シーズンのデータが見つかりません", flush=True)
        return

    articles_path = os.path.join(args.news_dir, "articles.json")
    articles = load_json(articles_path, [])

    try_generate_digest(args, season, articles_path, articles)
    try_generate_newcomer(args, season, articles_path, articles)
    try_generate_column(args, season, articles_path, articles)
    try_generate_upset(args, season, articles_path, articles)
    try_generate_milestone(args, season, articles_path, articles)


if __name__ == "__main__":
    main()
