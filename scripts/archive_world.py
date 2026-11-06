"""今の世界線（リセット前の全季）の結果を archive/<名前>/ に保存する。棋譜（各局の手順）は残さない。

使い方: python3 scripts/archive_world.py [data_dir] [出力先]
  出力先を省略すると archive/world_<最終季>_<日付>/ に保存する。

保存するもの
  rosters.json            リセット直前の現役選手（能力値・成績・師弟関係）
  season_state.json       引退者の記録・タイトル履歴（棋譜を除く）・保持者など
  standings.json.gz       各季の順位表（季→行の一覧）
  match_results.json.gz   各季の対局結果（誰が誰に勝ったか。棋譜は除く）
  newcomer_league.json.gz 各季の新人リーグの順位表（棋譜は除く）
  articles.json / awards.json / newcomer_winners.json  ファンサイトの記事・表彰など
  README.md               この世界線のあらまし（季数・タイトル獲得数の上位など）
"""
import collections
import datetime
import glob
import gzip
import json
import os
import re
import shutil
import sys

HEAVY_KEYS = ("games", "bracket", "moves", "match_log")


def _strip(obj):
    """棋譜（games・bracket・moves・match_log）を再帰的に取り除く"""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in HEAVY_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _season_files(pattern):
    out = {}
    for path in glob.glob(pattern):
        m = re.search(r"(\d+)\.json$", path)
        if m:
            with open(path, encoding="utf-8") as f:
                out[int(m.group(1))] = json.load(f)
    return dict(sorted(out.items()))


def _dump_gz(path, obj):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def archive(data_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    last = state.get("current_season", 0)

    with open(os.path.join(out_dir, "season_state.json"), "w", encoding="utf-8") as f:
        json.dump(_strip(state), f, ensure_ascii=False, separators=(",", ":"))
    for name in ("rosters.json", "newcomer_winners.json", "shoreikai.json", "newcomer_candidate_pool.json"):
        src = os.path.join(data_dir, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out_dir, name))
    for name in ("articles.json", "awards.json"):
        src = os.path.join(data_dir, "news", name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out_dir, name))

    _dump_gz(os.path.join(out_dir, "standings.json.gz"),
             _season_files(os.path.join(data_dir, "standings", "season_*.json")))
    matches = _season_files(os.path.join(data_dir, "matches", "season_*.json"))
    _dump_gz(os.path.join(out_dir, "match_results.json.gz"), {s: _strip(v) for s, v in matches.items()})
    nl = _season_files(os.path.join(data_dir, "newcomer_league", "for_season_*.json"))
    _dump_gz(os.path.join(out_dir, "newcomer_league.json.gz"), {s: _strip(v) for s, v in nl.items()})

    # あらまし
    with open(os.path.join(data_dir, "rosters.json"), encoding="utf-8") as f:
        rosters = json.load(f)
    everyone = [x for v in rosters.values() for x in v] + state.get("retired_archive", [])
    names = {x["id"]: x.get("display_name") for x in everyone}
    th = [e for e in state.get("title_history", []) if e.get("season") and e.get("holder_id")
          and e.get("event") != "初代襲名"]
    top = collections.Counter(e["holder_id"] for e in th).most_common(10)
    lines = [
        f"# 世界線アーカイブ（第1〜{last}季）",
        "",
        f"- 保存日: {datetime.date.today().isoformat()}",
        f"- 選手数: 現役{sum(len(v) for v in rosters.values())}人・引退{len(state.get('retired_archive', []))}人",
        f"- タイトル戦: {len(th)}件",
        "",
        "## 最終季のタイトル保持者",
        "",
    ]
    for title, info in (state.get("titleholders") or {}).items():
        lines.append(f"- {title}: {(info or {}).get('name', '空位')}")
    lines += ["", "## タイトル獲得数（保持した期数）の上位", ""]
    for i, (pid, n) in enumerate(top, 1):
        lines.append(f"{i}. {names.get(pid, pid)}: {n}期")
    lines += ["", "棋譜（各局の手順）は保存していない。ファイルの中身は scripts/archive_world.py の冒頭を参照。", ""]
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_dir


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    if len(sys.argv) > 2:
        out = sys.argv[2]
    else:
        with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
            last = json.load(f).get("current_season", 0)
        out = os.path.join("archive", f"world_{last}_{datetime.date.today().strftime('%Y%m%d')}")
    print(archive(data_dir, out))
