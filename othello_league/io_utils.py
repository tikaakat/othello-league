import json
import os

from .individual import LeagueIndividual


def load_rosters(data_dir):
    """4リーグ分のロスターを読み込む。無ければNoneを返す（初回起動の合図）"""
    path = os.path.join(data_dir, "rosters.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    rosters = {}
    for league in ("A", "B", "C", "D"):
        rosters[league] = [LeagueIndividual.from_dict(d) for d in raw.get(league, [])]
    return rosters


def save_rosters(data_dir, rosters):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "rosters.json")
    raw = {league: [ind.to_dict() for ind in members] for league, members in rosters.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def load_season_state(data_dir):
    """シーズン番号・タイトル保持者・名前レジストリ等、進行状態をまとめて読み込む"""
    path = os.path.join(data_dir, "season_state.json")
    if not os.path.exists(path):
        return {
            "current_season": 0,
            "name_registry": {},
            "titleholders": {"陸王": None, "海王": None, "空王": None},
            "titleholder_params": {"陸王": None, "海王": None, "空王": None},
            "retired_archive": [],
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_season_state(data_dir, state):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "season_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def save_match_log(data_dir, season, match_log):
    """このシーズンの全対局ログを、世代別ファイルとして保存する（肥大化を避けるため season 単位で分割）"""
    os.makedirs(os.path.join(data_dir, "matches"), exist_ok=True)
    path = os.path.join(data_dir, "matches", f"season_{season}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(match_log, f, ensure_ascii=False, indent=2)
