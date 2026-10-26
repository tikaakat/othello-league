"""
段階シードのトーナメント組み合わせ（試行中。まだ本番の季の進行には使っていない）。

格の高い選手ほど後の回戦から登場する（勝たなければならない回数が少ない）形にする。
各選手に「登場する段」（0＝1回戦から、1＝2回戦から、2＝3回戦から…）を与え、
段eの選手は 2^e 枠ぶんの場所を占める（隣の枠を空けておき、不戦勝で勝ち上がった位置から登場する）。
組み合わせは、上位の選手どうしができるだけ遅くまで当たらないよう、上半分・下半分に交互に振り分ける。
"""
import random

from .title import NEWCOMER_SLOT_PREFIX, QUALIFIER_SLOT_PREFIX


def tiered_slots(entries, size=None):
    """entries：[(ID, 登場する段)]（優先度の高い順）。トーナメントの並び（空き枠はNone）を返す。
    size（2のべき乗）を省くと、全員が収まる最小の大きさ以下で一番近い2のべき乗にする。
    - 枠が足りなければ、優先度の低い選手から2人ずつ「予備戦」（1回戦の前の1局）に回す
    - 枠が余れば、優先度の高い選手から順に、さらに1つ後の段から登場させる（不戦勝を与える）"""
    entries = [list(e) for e in entries]
    need = sum(2 ** e for _, e in entries)
    if size is None:
        size = 1
        while size * 2 <= need:
            size *= 2
    while need < size:
        for item in entries:
            if need + 2 ** item[1] <= size:
                need += 2 ** item[1]
                item[1] += 1
                break
        else:
            break
    playin = 0
    if need > size:
        # 予備戦に回る人数：はみ出した分の2倍（2人で1枠）。全員の段を1つ上げ、予備戦の選手だけ段0にする
        playin = 2 * (need - size)
        for k, item in enumerate(entries):
            item[1] = item[1] + 1 if k < len(entries) - playin else 0
        size *= 2

    def place(items, size):
        if not items:
            return [None] * size
        if size == 1:
            return [items[0][0]]
        if len(items) == 1 and 2 ** items[0][1] >= size:
            return [items[0][0]] + [None] * (size - 1)
        half = size // 2
        sides = [[], []]
        room = [half, half]
        turn = 0
        for item in items:
            w = 2 ** item[1]
            # 空きの多い側へ（同じなら交互に）。上位どうしを上下の山に分ける
            if room[0] == room[1]:
                k = turn
                turn ^= 1
            else:
                k = 0 if room[0] > room[1] else 1
            if room[k] < w:
                k ^= 1
            sides[k].append(item)
            room[k] -= w
        return place(sides[0], half) + place(sides[1], half)

    return place(entries, size)


def plan_byakko_bracket_v2(all_members, exclude_id=None, entrants=24, seeds=8, guaranteed_id=None,
                           titleholder_ids=frozenset()):
    """白虎戦：Elo上位entrants名（前季の失冠者は無条件で出場）。上位seeds名は2回戦から登場。
    シードの優先順：前季の失冠者 → ほかのタイトル保持者 → Elo順"""
    pool = [ind for ind in all_members if ind.id != exclude_id]
    by_elo = sorted(pool, key=lambda ind: -ind.elo)
    chosen = [ind for ind in by_elo if ind.id != guaranteed_id][:entrants - (1 if guaranteed_id else 0)]
    g = next((ind for ind in pool if ind.id == guaranteed_id), None)
    if g:
        chosen.append(g)
    prio = sorted(chosen, key=lambda ind: (0 if ind.id == guaranteed_id else (1 if ind.id in titleholder_ids else 2), -ind.elo))
    entries = [(ind.id, 1 if k < seeds else 0) for k, ind in enumerate(prio)]
    return tiered_slots(entries)


def plan_genbu_bracket_v2(all_members, exclude_id=None, num_blocks=4, titleholder_ids=frozenset(),
                          guaranteed_final_id=None, newcomer_slots=0, title_level=2, a_level=1):
    """玄武戦：全員参加の4ブロック。タイトル保持者はtitle_level段目（既定は3回戦）から、
    Aリーグの選手はa_level段目（既定は2回戦）から、ほかは1回戦から登場する。
    シードの選手はブロックに均等に振り分け（優先度順に蛇行）、それ以外はランダムに振り分ける。
    全ブロックを同じ大きさにし、ブロックを順につないだ並びを返す"""
    members = [ind for ind in all_members if ind.id != exclude_id and ind.id != guaranteed_final_id]
    def level(ind):
        if ind.id in titleholder_ids:
            return title_level
        return a_level if ind.league == "A" else 0
    seeded = sorted([ind for ind in members if level(ind) > 0], key=lambda ind: (-level(ind), -ind.elo))
    others = [ind.id for ind in members if level(ind) == 0] + [f"{NEWCOMER_SLOT_PREFIX}{k + 1}" for k in range(newcomer_slots)]
    random.shuffle(others)
    blocks = [[] for _ in range(num_blocks)]
    b, step = 0, 1
    for ind in seeded:
        blocks[b].append((ind.id, level(ind)))
        if not (0 <= b + step < num_blocks):
            step = -step
        else:
            b += step
    for pid in others:
        k = min(range(num_blocks), key=lambda i: sum(2 ** e for _, e in blocks[i]))
        blocks[k].append((pid, 0))
    # 全ブロック共通の大きさ：一番大きいブロックが収まる2のべき乗以下で一番近いもの（はみ出しは予備戦）
    need = max(sum(2 ** e for _, e in bl) for bl in blocks)
    size = 1
    while size * 2 <= need:
        size *= 2
    blocks_slots = [tiered_slots(bl, size=size) for bl in blocks]
    width = max(len(x) for x in blocks_slots)
    # 予備戦のあるブロックと無いブロックで長さが違う場合は、無い方を同じ大きさで作り直す（余った枠はシードの不戦勝になる）
    out = []
    for bl, x in zip(blocks, blocks_slots):
        out += x if len(x) == width else tiered_slots(bl, size=width)
    return out


def plan_kirin_main_v2(group1, needed_deep=4, main_size=16):
    """麒麟戦の本戦：1組の上位needed_deep名（並び順＝優先度：前季の失冠者→前季の成績順）は2回戦から、
    1組の残りと予選通過者は1回戦から。予選通過者の枠（Q1, Q2, ...）の数も返す"""
    deep = group1[:needed_deep]
    rest = group1[needed_deep:]
    q = max(0, main_size - 2 * len(deep) - len(rest))
    entries = [(pid, 1) for pid in deep] + [(pid, 0) for pid in rest] + [(f"{QUALIFIER_SLOT_PREFIX}{k + 1}", 0) for k in range(q)]
    return tiered_slots(entries, size=main_size), q
