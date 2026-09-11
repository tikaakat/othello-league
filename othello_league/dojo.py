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

AGE_BUFF_RANGES = [
    (0, 29, (0.95, 1.20)),
    (30, 39, (0.90, 1.10)),
    (40, 49, (0.85, 1.10)),
    (50, 59, (0.80, 1.05)),
    (60, 999, (0.75, 1.00)),
]


def _age_range_for(age):
    for lo, hi, rng in AGE_BUFF_RANGES:
        if lo <= age <= hi:
            return rng
    return AGE_BUFF_RANGES[-1][2]


def roll_age_multipliers(age, param_keys):
    """年齢帯に応じた倍率レンジから、パラメータごとに独立に抽選する。シーズン開始時に1回呼ぶ想定"""
    lo, hi = _age_range_for(age)
    return {key: round(random.uniform(lo, hi), 3) for key in param_keys}


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
    """個体の実効パラメータ（道場バフ・年齢バフを反映した後の値）を返す。評価関数にはこれを渡す"""
    params = individual.params
    if individual.dojo:
        params = apply_dojo_buff(params, individual.dojo, getattr(individual, "buff_multiplier", None))
    age_mult = getattr(individual, "age_multipliers", None)
    if age_mult:
        params = {k: v * age_mult.get(k, 1.0) for k, v in params.items()}
    return params


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
