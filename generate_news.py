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


def gather_season_facts(data_dir, season):
    """指定シーズンの対局結果から、記事生成に使う構造化データ（事実）を集める。
    タイトル戦の勝者側（奪取なら挑戦者、防衛ならホルダー）のIDを
    related_individual_idsとしてまとめて返す（記事の「関連個体」表示用）"""
    standings = load_json(os.path.join(data_dir, "standings", f"season_{season}.json"), [])
    matches = load_json(os.path.join(data_dir, "matches", f"season_{season}.json"), [])
    names = {r["individual_id"]: r["display_name"] for r in standings}

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
            "title": t,
            "challenger_name": names.get(a_id, a_id), "holder_name": names.get(b_id, b_id),
            "challenger_wins": win_a, "holder_wins": loss_a,
            "result": "奪取" if challenger_won else "防衛",
        })
        related.append((winner_id, names.get(winner_id, winner_id)))

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
    lines.append("")
    lines.append(
        f"以上の事実だけをもとに、第{facts['season']}季のダイジェスト記事を書いてください。"
        "データに無い出来事・数字は書かないこと。"
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
    try_generate_column(args, season, articles_path, articles)
    try_generate_milestone(args, season, articles_path, articles)


if __name__ == "__main__":
    main()
