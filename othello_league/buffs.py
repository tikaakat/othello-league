import random

from .skills import ENDGAME_KEY, effective_endgame, awaken_endgame

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
    """年齢帯に応じた倍率レンジから1つの倍率を抽選し、全パラメータに同じ倍率をかける。
    シーズン開始時に1回呼ぶ想定。
    以前はパラメータごとに独立に抽選していたが、評価は能力値の比率で指し手が決まるため、
    毎季の抽選で棋風（比率）そのものが変わり、強さの順位が季ごとにランダムに入れ替わっていた
    （季をまたいだ「期待以上の勝ち」の相関がほぼ0）。全パラメータ同じ倍率なら棋風は変わらず、
    倍率は1局ごとの評価ノイズに対する評価の大きさとして効く（倍率が低い＝相対的にブレが大きい＝
    やや弱い）。シミュレーションで、季をまたいだ強さの相関が0.63→0.82に上がることを確認した"""
    lo, hi = _age_range_for(age)
    m = round(random.uniform(lo, hi), 3)
    return {key: m for key in param_keys}


def effective_params(individual):
    """個体の実効パラメータ（年齢バフを反映した後の値）を返す。評価関数にはこれを渡す"""
    params = individual.params
    age_mult = getattr(individual, "age_multipliers", None)
    if age_mult:
        params = {k: v * age_mult.get(k, 1.0) for k, v in params.items()}
    if ENDGAME_KEY in params:
        # 終盤力はその季の調子で上下する（skills.py）
        params = dict(params)
        params[ENDGAME_KEY] = effective_endgame(individual)
    return params


# ============================================================
# 覚醒：ごく稀に、終盤力が一気に伸びる（skills.py）
# ============================================================
AWAKENING_CHANCE = 0.06  # 6%（1回の新人リーグで平均1.5人程度。3%では新人リーグを勝ち抜く覚醒個体がほとんど出なかったため引き上げ）
AWAKENING_MULTIPLIER_RANGE = (1.8, 3.0)


def maybe_awaken(params, individual_id=None, chance=AWAKENING_CHANCE):
    """paramsのコピーに対し、chanceの確率で覚醒させる。(新パラメータ, 覚醒したキー名 or None) を返す。
    以前は8つの能力のどれか1つを大きく伸ばしていたが、強さは能力の比率で決まるため比率が崩れるだけで
    強さの突破にならなかった。終盤力（skills.py）が一気に伸びる形に改めた"""
    if random.random() >= chance:
        return dict(params), None

    awakened = dict(params)
    awaken_endgame(awakened)
    if individual_id:
        print(f"  ★★★ 覚醒！ {individual_id} の終盤力が {awakened[ENDGAME_KEY]:.1f} に伸びました")
    return awakened, ENDGAME_KEY
