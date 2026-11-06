"""奨励会（三段リーグの下の育成段階）。

新人は12〜15歳で奨励会に入会し、師匠もこのとき決まる。奨励会では対局はせず、毎季年を取って
終盤力・定跡が伸びる（本戦と同じ成長の規則）。三段リーグ（新人リーグ）に空きができると、
奨励会から「強さの見積もり＋運」の順に上がる。三段リーグは対局（12局）で上位3名が本戦のDリーグへ。

年齢制限：奨励会は21歳まで（それまでに三段リーグへ上がれなければ退会）、三段リーグは26歳まで。

試算（scratchpadの試算、600季×3回）：毎季12名入会で奨励会は約65名・三段リーグは常に30名、
年齢制限による退会は毎季 奨励会約7名・三段約1.6名、デビュー年齢は平均21歳前後。
13〜15歳でのデビュー（中学生棋士）は25季に1人ほどで、その多くが後に一流になる
（入会時は終盤力・定跡が未熟で、才能＝伸びる速さのある子だけが若くして抜け出すため）。
"""
import copy
import math
import random
from types import SimpleNamespace

from .skills import BOOK_PARAMS
from .league import (
    PARAM_KEYS, _build_character_creation_individual, _pick_master, master_pool, NEW_FOUNDER_CHANCE,
)
from .skills import season_growth, ENDGAME_KEY, BOOK_KEY

SHOREIKAI_INTAKE = 12             # 毎季の入会者数
SHOREIKAI_ENTRY_AGE = (12, 15)
SHOREIKAI_AGE_LIMIT = 21          # この年齢を超えて奨励会に残ることはできない（三段に上がれなければ退会）
SANDAN_AGE_LIMIT = 26             # 三段リーグの年齢制限
SANDAN_SIZE = 30                  # 三段リーグの定員
PROMOTION_LUCK = 40.0             # 奨励会→三段の昇段の運（強さの見積もりに足すばらつき、レート換算）
YOUTH_ENDGAME_SCALE = 0.1         # 入会時の終盤力は、生まれつきの値のこの割合（まだ未熟）
YOUTH_BOOK_PER_YEAR = 0.5         # 入会時の定跡：（年齢−10）×この手数 程度
CHARACTER_TYPES = ["balanced", "aggressive", "defensive", "corner"]


def estimated_strength(params):
    """強さの大まかな見積もり（レート換算）：終盤力・定跡の効き目＋棋風が強い配分にどれだけ近いか
    ＋天賦の効き目（天賦は1マスあたり約+2.5、大天才の2段目は約+9）。run_season._estimated_strengthと同じ式に天賦を足したもの"""
    eg = params.get(ENDGAME_KEY, 0.0)
    eg_elo = 67 * eg / 24 if eg <= 24 else 67 + (min(eg, 44) - 24) * (168 - 67) / 20
    book = params.get(BOOK_KEY, 0)
    book_elo = 113 * min(book, 16) / 16 + (10 * (min(book, 24) - 16) / 8 if book > 16 else 0)
    a = [params[k] for k in PARAM_KEYS]
    b = [BOOK_PARAMS[k] for k in PARAM_KEYS]
    cos = sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return eg_elo + book_elo + (cos - 0.94) * 1500 + 2.5 * params.get("_genius", 0) + 9 * params.get("_genius2", 0)


def grow_entry(entry):
    """1季分、年を取って成長する（本戦と同じ規則。entryのparamsを更新し、ageを1つ進める）"""
    entry["age"] = entry.get("age", 18) + 1
    holder = SimpleNamespace(age=entry["age"], params=entry["params"])
    season_growth(holder)


def _new_member(registry, master, season, index):
    name, gender = registry.generate()
    age = random.randint(*SHOREIKAI_ENTRY_AGE)
    request = {"name": name, "gender": gender, "type": random.choice(CHARACTER_TYPES),
               "auto_generated": True, "initial_age": age}
    ind = _build_character_creation_individual(request, season="SK", index=index, master=master)
    p = ind.params
    # 入会時はまだ未熟：終盤力は生まれつきの1割、定跡は（年齢−10）×0.5手程度
    p[ENDGAME_KEY] = round(p.get(ENDGAME_KEY, 0.0) * YOUTH_ENDGAME_SCALE, 2)
    p[BOOK_KEY] = int(max(0, (age - 10) * YOUTH_BOOK_PER_YEAR * random.uniform(0.6, 1.4)))
    return {
        "name": name, "gender": gender, "type": request["type"], "params": dict(p),
        "awakened_param": ind.awakened_param, "auto_generated": True, "age": age,
        "parent_a_id": ind.parent_a_id,
        # 新規開祖（師匠なし）は仮IDを指すので消しておく（本戦参入時に自分の実IDが系統の祖になる）
        "clan_root_id": ind.clan_root_id if ind.clan_root_id != ind.id else None,
        "generation": ind.generation, "volatility": ind.volatility,
        "joined_season": season, "entry_age": age,
    }


def _admit(registry, season, pool, retired_records, titleholder_ids, count):
    active = [ind for ind in (pool or []) if not ind.retired]
    masters = master_pool(active, retired_records, season) if active else []
    picks = {}
    out = []
    for i in range(count):
        if not masters or random.random() < NEW_FOUNDER_CHANCE:
            master = None
        else:
            master = _pick_master(masters, titleholder_ids, allow_new_founder=False, pick_counts=picks)
        out.append(_new_member(registry, master, season, i))
    return out


def bootstrap(registry, season, pool, retired_records, titleholder_ids):
    """奨励会がまだ無いとき（導入時・リセット後）に、入会から数年たった状態の奨励会を作る
    （過去SHOREIKAI_AGE_LIMIT−12季分の入会者を、入会からの年数だけ成長させて並べる）"""
    members = []
    for back in range(SHOREIKAI_AGE_LIMIT - SHOREIKAI_ENTRY_AGE[0], -1, -1):
        batch = _admit(registry, season - back, pool, retired_records, titleholder_ids, SHOREIKAI_INTAKE)
        for e in batch:
            for _ in range(back):
                grow_entry(e)
        members += [e for e in batch if e["age"] <= SHOREIKAI_AGE_LIMIT]
    return members


def advance(sandan, shoreikai, registry, season, pool, retired_records, titleholder_ids,
            master_names=None, submissions_count=0):
    """新人リーグ（三段リーグ）の前に1季分を進める。
    sandan：前季に昇格できなかった三段リーグの在籍者（newcomer_candidate_pool.json）
    shoreikai：奨励会の在籍者（Noneなら初回として作る）
    戻り値：(今季の三段リーグの出場者, 奨励会の在籍者, 記録)"""
    master_names = master_names or {}
    log = {"joined": [], "to_sandan": [], "left": []}
    first = shoreikai is None
    if first:
        shoreikai = bootstrap(registry, season, pool, retired_records, titleholder_ids)
    else:
        for e in sandan + shoreikai:
            grow_entry(e)
    kept = []
    for e in sandan:
        if e["age"] > SANDAN_AGE_LIMIT:
            log["left"].append({"name": e["name"], "age": e["age"], "reason": "三段リーグの年齢制限",
                                "master_name": master_names.get(e.get("parent_a_id"))})
        else:
            kept.append(e)
    sandan = kept
    kept = []
    for e in shoreikai:
        if e["age"] > SHOREIKAI_AGE_LIMIT:
            log["left"].append({"name": e["name"], "age": e["age"], "reason": "奨励会の年齢制限",
                                "master_name": master_names.get(e.get("parent_a_id"))})
        else:
            kept.append(e)
    shoreikai = kept
    if not first:
        new = _admit(registry, season, pool, retired_records, titleholder_ids, SHOREIKAI_INTAKE)
        shoreikai += new
        log["joined"] = [{"name": e["name"], "age": e["age"], "master_name": master_names.get(e.get("parent_a_id"))}
                         for e in new]
    # 三段リーグの空き（投稿されたキャラクリエイト分は定員の外）を、強さの見積もり＋運の順で埋める
    vacancy = max(0, SANDAN_SIZE - len(sandan))
    ranked = sorted(shoreikai, key=lambda e: estimated_strength(e["params"]) + random.gauss(0, PROMOTION_LUCK),
                    reverse=True)
    up, shoreikai = ranked[:vacancy], ranked[vacancy:]
    log["to_sandan"] = [{"name": e["name"], "age": e["age"], "master_name": master_names.get(e.get("parent_a_id"))}
                        for e in up]
    sandan = sandan + up
    shoreikai.sort(key=lambda e: (e.get("joined_season", 0), e["name"]))
    return sandan, shoreikai, log
