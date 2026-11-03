"""
棋風（8つの能力の比率）とは別の「実力の軸」：終盤力・定跡・調子。

8つの能力は指し手の比率だけを決めるため、最強の比率が存在し、そこに近づくほど頭打ちになる。
そこで、比率を変えずに強さの差をつける軸を足す（シミュレーションで効き目と所要時間を確認済み）。

- 終盤力（_endgame）：空きマスがこの数以下になったら、読みを1手深くする。
  空き24マス以下で約+67、32マスで約+115、44マスで約+168（レート換算）。それ以下では効きにくく、
  44を超えると時間がかかる割に伸びないため上限を44とする。
  生まれつきは低く（大半が0〜5マス）、成長の速さ（_eg_growth）に大きな個人差があり、
  30歳まで伸び、37歳まで半分の速さ、49歳まで横ばい、50歳から衰える。覚醒すると大きく伸びる。
- 定跡（_book）：序盤のこの手数までは、世間で知られた定跡（共通の強い評価・揺らぎなし）で指す。
  16手まで知っていると約+113、24手で約+123。対局を重ねるほど増え（増え方に個人差、_book_rate）、衰えない。
  デビュー時に師匠の定跡の7割（最低16手）を受け継ぎ、30歳未満は1.3倍の速さで覚える（若手が育つ前に辞めてしまわないように）。
  読みの量は普段と同じなので、所要時間は増えない。
- 調子（_form）：季ごとの好不調。その季の終盤力が上下する。ムラ気（volatility）が大きいほど振れ幅が大きい。

いずれもparamsの中に「_」で始まるキーとして持たせる（評価関数は8つの能力のキーしか見ないため影響しない）。
"""
import math
import random

ENDGAME_KEY = "_endgame"
ENDGAME_GROWTH_KEY = "_eg_growth"
BOOK_KEY = "_book"
BOOK_RATE_KEY = "_book_rate"
FORM_KEY = "_form"
GENIUS_KEY = "_genius"             # 天賦：空きマスがこの数以下（かつ終盤力の範囲内）なら、さらにもう1手深く読む
GENIUS_MAX_KEY = "_genius_max"     # 天賦のピーク（その選手が伸びきったときの範囲）
SKILL_KEYS = (ENDGAME_KEY, ENDGAME_GROWTH_KEY, BOOK_KEY, BOOK_RATE_KEY, FORM_KEY, GENIUS_KEY, GENIUS_MAX_KEY)

ENDGAME_CAP = 44.0
ENDGAME_BIRTH_MAX = 20.0          # 生まれつき：20 × Beta(1, 3)（平均5マス、10マス超は3割弱）
ENDGAME_BIRTH_BETA = 3.0
ENDGAME_INHERIT = 0.2             # 師匠の終盤力の2割を受け継ぐ
ENDGAME_GROWTH_MEDIAN = 0.4       # 成長の速さ（1季あたりのマス数）：中央値0.4、ばらつき大（一部が速く伸びる）
ENDGAME_GROWTH_SIGMA = 1.5
ENDGAME_GROWTH_MAX = 6.0
ENDGAME_DECLINE = 1.5             # 50歳以降、成長の速さ×1.5ずつ衰える
AWAKENING_ENDGAME_RANGE = (10.0, 20.0)

BOOK_CAP = 24
BOOK_RATE_RANGE = (1.0, 3.0)      # 1季あたりに覚える定跡の手数（個人差）
BOOK_INHERIT = 0.7                # デビュー時に師匠の定跡の7割を受け継ぐ
# 新人が最初から知っている定跡の最低手数。以前は師匠の3割（中央値11手）だけで、上限（24手）まで覚えたDリーグの
# ベテランに序盤で差を付けられ、素質のある若手もDで負け越して早々にフリークラスへ落ちていた（1季目の勝率31%）
BOOK_NEWCOMER_MIN = 16
BOOK_YOUNG_AGE = 30               # この年齢未満は定跡を早く覚える
BOOK_YOUNG_SPEEDUP = 1.3          # 若手の定跡の覚える速さ（倍率）

# フリークラスで過ごした季の終盤力の伸び（倍率）。以前はフリークラスでも同じだけ伸び、伸びる前の若手が
# フリークラスに落ちて育ってから戻ってくる形が目立っていた（フリークラスは力の足りない選手が落ちる場所にしたい）
FREE_CLASS_GROWTH_SCALE = 0.5
FORM_SCALE = 3.0
# 天賦（まれに生まれる天才）：トップ選手は終盤力・定跡が上限で横並びになり、別格の選手が生まれなかった
# （終盤力は定跡と合わせて36あれば対局のほぼ全部を覆うので、上限を上げても差は付かない）。
# 天賦を持つ選手だけ、終盤でさらに1手深く読む。トップ選手どうしの対局で、ピーク（空き20〜28マス）の天才は
# 1局57〜70%・7番勝負65〜87%で勝つ。若いうちに伸び、45歳を過ぎると狭まる
GENIUS_CHANCE = 1 / 40             # 新人のうち天賦を持って生まれる割合（1季3人の入門で十数季に1人）
GENIUS_PEAK_RANGE = (20, 28)
GENIUS_GROWTH = 4                  # 30歳未満の1季あたりの伸び（マス）
GENIUS_DECLINE_AGE = 45
GENIUS_DECLINE = 2                 # 衰える年齢からの1季あたりの縮み（マス）                  # 調子：その季の終盤力が ±（ムラ気×3マス）程度上下する

# 定跡として使う共通の評価（シミュレーションで最も強かった配分。合計40）
BOOK_PARAMS = {
    "corner_weight": 8.22, "danger_zone_weight": 6.37, "mobility_weight": 2.05, "edge_stability_weight": 1.70,
    "frontier_weight": 10.23, "disc_weight": 3.52, "parity_weight": 5.10, "center_weight": 2.81,
}


def _growth_speed():
    return min(ENDGAME_GROWTH_MAX, random.lognormvariate(math.log(ENDGAME_GROWTH_MEDIAN), ENDGAME_GROWTH_SIGMA))


def _age_factor(age):
    if age < 30:
        return 1.0
    if age < 38:
        return 0.5
    if age < 50:
        return 0.0
    return -ENDGAME_DECLINE


def birth_skills(master_params=None):
    """新しく生まれる個体の終盤力・定跡を決める（師匠がいれば終盤力を2割、定跡を7割受け継ぐ。定跡は最低16手）"""
    endgame = ENDGAME_BIRTH_MAX * random.betavariate(1.0, ENDGAME_BIRTH_BETA)
    if master_params:
        endgame += ENDGAME_INHERIT * master_params.get(ENDGAME_KEY, 0.0)
    return {
        ENDGAME_KEY: round(min(ENDGAME_CAP, endgame), 2),
        ENDGAME_GROWTH_KEY: round(_growth_speed(), 3),
        BOOK_KEY: min(BOOK_CAP, max(BOOK_NEWCOMER_MIN, int(BOOK_INHERIT * (master_params or {}).get(BOOK_KEY, 0)))),
        BOOK_RATE_KEY: round(random.uniform(*BOOK_RATE_RANGE), 2),
        **({GENIUS_KEY: 4, GENIUS_MAX_KEY: random.randint(*GENIUS_PEAK_RANGE)} if random.random() < GENIUS_CHANCE else {}),
    }


def veteran_skills(age):
    """（初期化・移行用）この年齢までキャリアを積んできた個体の終盤力・定跡を、成長の規則どおりに逆算して作る"""
    s = birth_skills()
    endgame = s[ENDGAME_KEY]
    for a in range(20, age):
        endgame = min(ENDGAME_CAP, max(0.0, endgame + s[ENDGAME_GROWTH_KEY] * _age_factor(a + 1)))
    s[ENDGAME_KEY] = round(endgame, 2)
    s[BOOK_KEY] = int(min(BOOK_CAP, s[BOOK_RATE_KEY] * max(0, age - 20)))
    return s


def ensure_skills(ind):
    """終盤力・定跡を持っていない個体（この仕組みの導入前からの個体）に、年齢相応の値を与える"""
    if ENDGAME_KEY not in ind.params:
        ind.params.update(veteran_skills(ind.age))


def awaken_endgame(params):
    """覚醒：終盤力が一気に伸びる"""
    params[ENDGAME_KEY] = round(min(ENDGAME_CAP, params.get(ENDGAME_KEY, 0.0) + random.uniform(*AWAKENING_ENDGAME_RANGE)), 2)


def roll_form(ind):
    """季の初めに調子を抽選する（その季の終盤力が上下する。表示用にparamsへ保存する）"""
    if ENDGAME_KEY in ind.params:
        ind.params[FORM_KEY] = round(random.gauss(0.0, 1.0) * ind.volatility * FORM_SCALE, 2)


def effective_endgame(ind):
    base = ind.params.get(ENDGAME_KEY, 0.0)
    return max(0.0, min(ENDGAME_CAP, base + ind.params.get(FORM_KEY, 0.0)))


def season_growth(ind, growth_scale=1.0):
    """季の終わりに、終盤力の成長・衰えと、定跡の習得を反映する（年齢は既に1つ進んでいる前提）。
    growth_scale：終盤力の伸び（成長期のプラス分だけ）にかける倍率。フリークラスで過ごした季は
    FREE_CLASS_GROWTH_SCALE（実戦が少ないので伸びにくい）。衰えには掛けない"""
    p = ind.params
    if ENDGAME_KEY not in p:
        return
    delta = p.get(ENDGAME_GROWTH_KEY, 0.0) * _age_factor(ind.age)
    if delta > 0:
        delta *= growth_scale
    p[ENDGAME_KEY] = round(min(ENDGAME_CAP, max(0.0, p[ENDGAME_KEY] + delta)), 2)
    if p.get(GENIUS_MAX_KEY):
        if ind.age < 30:
            p[GENIUS_KEY] = min(p[GENIUS_MAX_KEY], p.get(GENIUS_KEY, 0) + GENIUS_GROWTH)
        elif ind.age >= GENIUS_DECLINE_AGE:
            p[GENIUS_KEY] = max(0, p.get(GENIUS_KEY, 0) - GENIUS_DECLINE)
    young = BOOK_YOUNG_SPEEDUP if ind.age < BOOK_YOUNG_AGE else 1.0
    p[BOOK_KEY] = int(min(BOOK_CAP, p.get(BOOK_KEY, 0) + p.get(BOOK_RATE_KEY, 0.0) * young + random.random()))
