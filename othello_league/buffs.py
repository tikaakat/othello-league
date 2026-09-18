import random

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


def effective_params(individual):
    """個体の実効パラメータ（年齢バフを反映した後の値）を返す。評価関数にはこれを渡す"""
    params = individual.params
    age_mult = getattr(individual, "age_multipliers", None)
    if age_mult:
        params = {k: v * age_mult.get(k, 1.0) for k, v in params.items()}
    return params


# ============================================================
# 覚醒：ごく稀に、通常の変異幅を超えて1パラメータが大きく伸びる
# ============================================================
AWAKENING_CHANCE = 0.03  # 3%（旧1%は80季程度回しても1体しか出ず稀すぎたため引き上げ）
AWAKENING_MULTIPLIER_RANGE = (1.8, 3.0)


def maybe_awaken(params, individual_id=None, chance=AWAKENING_CHANCE):
    """paramsのコピーに対し、chanceの確率で1パラメータを大きく突破させる。(新パラメータ, 突破したキー名 or None) を返す"""
    if random.random() >= chance:
        return dict(params), None

    awakened = dict(params)
    key = random.choice(list(awakened.keys()))
    mult = random.uniform(*AWAKENING_MULTIPLIER_RANGE)
    awakened[key] = round(awakened.get(key, 1.0) * mult, 3)
    if individual_id:
        print(f"  ★★★ 覚醒！ {individual_id} の「{key}」が突破しました（×{mult:.2f}）")
    return awakened, key
