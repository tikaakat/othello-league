import random

# 8大流派：それぞれ1つのパラメータを得意分野として持つ。
# バフ倍率は個体ごとに少しばらつかせてよい（お任せ、とのことなので緩やかな幅を持たせる）
DOJO_BUFF_PARAM = {
    "真紅": "corner_weight",
    "紺碧": "danger_zone_weight",
    "翡翠": "mobility_weight",
    "琥珀": "edge_stability_weight",
    "紫苑": "frontier_weight",
    "白銀": "disc_weight",
    "黄金": "parity_weight",
    "漆黒": "center_weight",
}

MAJOR_DOJOS = list(DOJO_BUFF_PARAM.keys())

BUFF_MULTIPLIER_RANGE = (1.05, 1.20)  # 道場バフの倍率レンジ（個体ごとに少しばらつく）


def assign_buff_multiplier():
    return round(random.uniform(*BUFF_MULTIPLIER_RANGE), 3)


def apply_dojo_buff(params, dojo, buff_multiplier=None):
    """道場所属個体のパラメータに、その道場の得意分野バフを適用した新しい辞書を返す"""
    if not dojo or dojo not in DOJO_BUFF_PARAM:
        return dict(params)
    buffed = dict(params)
    key = DOJO_BUFF_PARAM[dojo]
    mult = buff_multiplier if buff_multiplier is not None else BUFF_MULTIPLIER_RANGE[0]
    buffed[key] = buffed.get(key, 1.0) * mult
    return buffed


def effective_params(individual):
    """個体の実効パラメータ（道場バフを反映した後の値）を返す。評価関数にはこれを渡す"""
    if individual.dojo:
        return apply_dojo_buff(individual.params, individual.dojo, getattr(individual, "buff_multiplier", None))
    return individual.params


# ============================================================
# 覚醒：ごく稀に、通常の変異幅を超えて1パラメータが大きく伸びる
# ============================================================
AWAKENING_CHANCE = 0.01  # 1%
AWAKENING_MULTIPLIER_RANGE = (1.8, 3.0)


def maybe_awaken(params, individual_id=None):
    """paramsのコピーに対し、1%の確率で1パラメータを大きく突破させる。(新パラメータ, 突破したキー名 or None) を返す"""
    if random.random() >= AWAKENING_CHANCE:
        return dict(params), None

    awakened = dict(params)
    key = random.choice(list(awakened.keys()))
    mult = random.uniform(*AWAKENING_MULTIPLIER_RANGE)
    awakened[key] = round(awakened.get(key, 1.0) * mult, 3)
    if individual_id:
        print(f"  ★★★ 覚醒！ {individual_id} の「{key}」が突破しました（×{mult:.2f}）")
    return awakened, key
