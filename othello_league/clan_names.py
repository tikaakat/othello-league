"""流（一門）の呼び名。開祖の苗字を取って「西村流」のように呼ぶ。
同じ苗字の開祖が複数いる場合は、先に興した流が苗字だけを名乗り、後の流は開祖のフルネームで呼ぶ（例：野口彩流）"""
from .names import SURNAMES


def surname_of(display_name):
    """表示名から苗字を取り出す（名前は苗字リスト＋名で作られているので、最長一致で切る）"""
    name = display_name or ""
    hits = [s for s in SURNAMES if name.startswith(s)]
    return max(hits, key=len) if hits else name


def clan_names(founders):
    """founders：[(開祖ID, 開祖の表示名, 興した季)]。戻り値：{開祖ID: 流の呼び名（「流」は含まない）}"""
    out, taken = {}, set()
    for cid, name, _ in sorted(founders, key=lambda f: (f[2] or 0, f[0])):
        sur = surname_of(name)
        if sur in taken:
            out[cid] = name
        else:
            out[cid] = sur
            taken.add(sur)
    return out


def clan_names_from_data(rosters, state, standings_first_season):
    """rosters.json・season_state.jsonから、3名以上の流の呼び名を作る。
    standings_first_season：{ID: 初めて対局した季}（独立していない流の興した季＝開祖のデビュー季）"""
    members = [m for ms in rosters.values() for m in ms] + state.get("retired_archive", [])
    by_id = {m["id"]: m for m in members}
    size = {}
    for m in members:
        size[m.get("clan_root_id")] = size.get(m.get("clan_root_id"), 0) + 1
    indep = {e["id"]: e["season"] for e in state.get("clan_independence_history", [])}
    founders = [(cid, by_id[cid]["display_name"], indep.get(cid, standings_first_season.get(cid, 0)))
                for cid, n in size.items() if cid in by_id and n >= 3]
    return clan_names(founders)
