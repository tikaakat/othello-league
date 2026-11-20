"""
棋風（8つの能力の比率）とは別の「実力の軸」：終盤力・定跡・調子。

8つの能力は指し手の比率だけを決めるため、最強の比率が存在し、そこに近づくほど頭打ちになる。
そこで、比率を変えずに強さの差をつける軸を足す（シミュレーションで効き目と所要時間を確認済み）。

- 終盤力（_endgame）：空きマスがこの数以下になったら、読みを1手深くする。
  空き24マス以下で約+67、32マスで約+115、44マスで約+168（レート換算）。それ以下では効きにくく、
  44を超えると時間がかかる割に伸びないため上限を44とする。
  生まれつきは低く（大半が0〜5マス）、成長の速さ（_eg_growth）に大きな個人差があり、
  25歳まで1.6倍の速さで伸び、29歳まで1倍、34歳まで0.4倍、44歳まで横ばい、45歳から衰える。覚醒すると大きく伸びる。
- 定跡（_book）：序盤のこの手数までは、世間で知られた定跡（共通の強い評価・揺らぎなし）で指す。
  16手まで知っていると約+113、24手で約+123。対局を重ねるほど増え（増え方に個人差、_book_rate）、衰えない。
  デビュー時に師匠の定跡の7割（最低16手）を受け継ぎ、30歳未満は1.3倍の速さで覚える（若手が育つ前に辞めてしまわないように）。
  読みの量は普段と同じなので、所要時間は増えない。
- 調子（_form）：季ごとの好不調。その季の終盤力が上下する。ムラ気（volatility）が大きいほど振れ幅が大きい。
- 読みの深さ（_read）：中盤以降の毎手の読みの深さの補正（−1〜+1手）。端数は確率で、+0.5なら半分の手で1手深く読む。
  棋風・終盤力・定跡だけでは選手どうしの差が小さく（同じ深さで読むため）、リーグの上下で勝率がほぼ五分になり、
  タイトル保持者が下のリーグに残る・衰えたベテランがAに残るといった状態が続いていた。
  試算（同じ選手どうし）：−1手で勝率24%（約−200）、+1手で69%（約+135）。16人の総当たりで、−1〜+1を割り振ると
  レートの標準偏差が約100→約140、上位4人の下位4人への勝率が85→91%。1局の時間は平均で約1.5倍。
  ピーク（_read_max）は才能で決まる（平均+0.2の正規分布、−1〜+2。天賦は+1.2以上、大天才は+1.4以上）。
  入会時はピークより1手浅い。25歳まで速く伸び、30歳まで少し、45歳から衰える（衰えの速さは_agingに従う）。
- ポカ率（_blunder）：その手で最善手以外を指してしまう確率。若いうちは高く経験で下限（才能）まで下がり、45歳から増える。

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
PRODIGY_KEY = "_genius2"           # （旧）大天才の2段目の範囲。今は使わない（tone_down_prodigy参照）
PRODIGY_MAX_KEY = "_genius2_max"   # 大天才の目印（値は旧2段目のピーク。今は目印としてだけ使う）
AGING_KEY = "_aging"               # 衰えの速さの倍率（1.0＝ふつう。一部の選手は衰えが緩やか。表には出さない）
READ_KEY = "_read"                 # 読みの深さの補正（−1〜+1手。端数は確率。engine.choose_move参照）
READ_MAX_KEY = "_read_max"         # 読みの深さのピーク（才能）
SKILL_KEYS = (ENDGAME_KEY, ENDGAME_GROWTH_KEY, BOOK_KEY, BOOK_RATE_KEY, FORM_KEY, GENIUS_KEY, GENIUS_MAX_KEY,
              PRODIGY_KEY, PRODIGY_MAX_KEY, AGING_KEY, READ_KEY, READ_MAX_KEY, "_blunder", "_blunder_min")

READ_FLOOR = -1.0
READ_CEIL = 2.0
# ピーク（才能）は平均+0.2・ばらつき0.45の正規分布（−1〜+2）。以前は−0.8〜+1.0に一様に写して上限で切っていたため、
# 三段リーグを抜けるプロの約半数が上限（1.0）に並び、プロどうしの差にならなかった（第55季でA0.75・D0.74）。
# +1を超えるのは一部の才能のある選手だけなので、全体の読む深さの平均（＝所要時間）はほぼ変わらない
READ_PEAK_MEAN = 0.2
READ_PEAK_SD = 0.45
READ_TALENT_WEIGHT = 0.3           # 終盤力の伸びる速さ（才能の共通因子）にどれだけ寄せるか
READ_START_GAP = 1.0               # 生まれたときはピークよりこれだけ浅い
READ_GENIUS_MIN = 1.2              # 天賦を持つ選手のピークの下限
READ_PRODIGY_MIN = 1.4             # 大天才のピークの下限（以前は1.6。tone_down_prodigy参照）
READ_GROWTH_YOUNG = 0.12           # 25歳未満の1季あたりの伸び
READ_GROWTH_MID = 0.06             # 30歳未満
READ_DECLINE_AGE = 45
READ_DECLINE = 0.06                # 45歳からの1季あたりの衰え（×_aging）

ENDGAME_CAP = 44.0
ENDGAME_BIRTH_MAX = 20.0          # 生まれつき：20 × Beta(1, 3)（平均5マス、10マス超は3割弱）
ENDGAME_BIRTH_BETA = 3.0
ENDGAME_INHERIT = 0.2             # 師匠の終盤力の2割を受け継ぐ
ENDGAME_GROWTH_MEDIAN = 0.4       # 成長の速さ（1季あたりのマス数）：中央値0.4、ばらつき大（一部が速く伸びる）
ENDGAME_GROWTH_SIGMA = 1.5
ENDGAME_GROWTH_MAX = 6.0
ENDGAME_DECLINE = 0.75            # 45歳以降、成長の速さ×0.75ずつ衰える（以前の1.5では40代後半で急に崩れ、最盛期が10代後半〜20代前半まで若くなりすぎた）
SLOW_AGING_CHANCE = 0.2           # 衰えが緩やかな選手の割合（息の長いベテラン）
SLOW_AGING_RANGE = (0.2, 0.5)     # その選手の衰え（終盤力・天賦）の倍率
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
GENIUS_CHANCE = 1 / 80             # 奨励会の入会者のうち天賦を持って生まれる割合（毎季12名入会・約半数がデビューし、十数季に1人）
GENIUS_PEAK_RANGE = (20, 28)
GENIUS_GROWTH = 4                  # 30歳未満の1季あたりの伸び（マス）
GENIUS_DECLINE_AGE = 45
GENIUS_DECLINE = 2                 # 衰える年齢からの1季あたりの縮み（マス）
# 大天才（羽生善治級）：50季に1人ほど。天賦の範囲が天賦の上限より少し広く（26マス）、読みの深さ・ポカ率も別格。
# 以前は藤井聡太級（天賦36マス＋終盤24マスでさらに1手・読み切りも広い・読み+1.6・ポカ0.5%）で、トップ相手に
# 1局約75%・7番勝負9割超で勝ち、第82〜100季に五冠を独占し続けた（防衛96勝4敗）。羽生善治の全盛期
# （番勝負の勝率およそ7〜8割）くらいを目安に弱め、対局試験（トップ10人が相手・長考）で1局約?%・7番勝負約?%
PRODIGY_CHANCE = 1 / 600           # 奨励会の入会者のうち大天才として生まれる割合（毎季12名入会、ほぼ全員がデビューし約50季に1人）
PRODIGY_PEAK = 26
PRODIGY2_PEAK = 24                 # 大天才の目印（PRODIGY_MAX_KEY）に入れる値。2段目の読みはもう使わない
PRODIGY_GROWTH = 6                 # 大天才の30歳未満の1季あたりの伸び（デビューが遅めでも30歳までにピークへ届くように）                  # 調子：その季の終盤力が ±（ムラ気×3マス）程度上下する

# 定跡として使う共通の評価（シミュレーションで最も強かった配分。合計40）
BOOK_PARAMS = {
    "corner_weight": 8.22, "danger_zone_weight": 6.37, "mobility_weight": 2.05, "edge_stability_weight": 1.70,
    "frontier_weight": 10.23, "disc_weight": 3.52, "parity_weight": 5.10, "center_weight": 2.81,
}


def _growth_speed():
    return min(ENDGAME_GROWTH_MAX, random.lognormvariate(math.log(ENDGAME_GROWTH_MEDIAN), ENDGAME_GROWTH_SIGMA))


def _book_rate_for(growth):
    """才能の共通因子：定跡を覚える速さを、終盤力の伸びる速さと連動させる（伸びる子は覚えるのも速い）。
    伸びる速さの順位（対数正規分布の累積）を1〜3手/季に写し、少しだけばらつかせる。
    以前は独立に抽選していたため、若くして頭角を現す子（定跡を早く覚える）が、その後大成するとは限らなかった"""
    z = math.log(max(growth, 1e-3) / ENDGAME_GROWTH_MEDIAN) / ENDGAME_GROWTH_SIGMA
    pct = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    pct = min(1.0, max(0.0, pct + random.gauss(0, 0.1)))
    lo, hi = BOOK_RATE_RANGE
    return round(lo + (hi - lo) * pct, 2)


def _read_max_for(p):
    """読みの深さのピーク（正規分布。伸びる子ほど少し深く読めるようになりやすい）。天賦・大天才は深い"""
    z = math.log(max(p.get(ENDGAME_GROWTH_KEY, ENDGAME_GROWTH_MEDIAN), 1e-3) / ENDGAME_GROWTH_MEDIAN) / ENDGAME_GROWTH_SIGMA
    z = max(-2.5, min(2.5, z))
    w = READ_TALENT_WEIGHT
    peak = READ_PEAK_MEAN + READ_PEAK_SD * (w * z + math.sqrt(1 - w * w) * random.gauss(0, 1))
    if p.get(PRODIGY_MAX_KEY):
        peak = max(peak, READ_PRODIGY_MIN)
    elif p.get(GENIUS_MAX_KEY):
        peak = max(peak, READ_GENIUS_MIN)
    return round(max(READ_FLOOR, min(READ_CEIL, peak)), 2)


# 読みの深さ→強さ（レート換算。同じ選手どうしの実測：−1手24%・+1手69%・+2手91%）
READ_ELO_POINTS = ((-1.0, -204.0), (0.0, 0.0), (1.0, 135.0), (2.0, 399.0))


def read_elo(r):
    for (x0, y0), (x1, y1) in zip(READ_ELO_POINTS, READ_ELO_POINTS[1:]):
        if r <= x1:
            return y0 + (y1 - y0) * (max(r, x0) - x0) / (x1 - x0)
    return READ_ELO_POINTS[-1][1]


# ---- ポカ率（_blunder）：その手で最善手以外の合法手を指してしまう確率（engine.choose_move）----
# 読みの深さと違って所要時間が増えず、上限も無いので、プロどうしでも差が残る。
# 実測（同じ選手どうし）：ポカ率3%で約−50、12%で約−140、20%で約−290（1あたり約−1300）。
# 生まれつきの下限（_blunder_min）は中央値4%の対数正規分布（大天才はほぼ0）。入会時は下限+8%で、
# 45歳までは1季ごとに下限との差が15%ずつ縮み（フリークラスでは半分）、45歳からは1季0.6%ずつ増える（×_aging）
BLUNDER_KEY = "_blunder"
BLUNDER_MIN_KEY = "_blunder_min"
BLUNDER_MIN_MEDIAN = 0.04
BLUNDER_MIN_SIGMA = 0.6
BLUNDER_MIN_CAP = 0.15
BLUNDER_PRODIGY_MAX = 0.012        # 大天才のポカ率の下限の上限（以前は0.5%）
BLUNDER_BIRTH_EXTRA = 0.08
BLUNDER_LEARN = 0.85
BLUNDER_DECLINE = 0.006
BLUNDER_CAP = 0.3
BLUNDER_ELO_PER = -1300.0


def _blunder_min_for(p):
    bm = min(BLUNDER_MIN_CAP, random.lognormvariate(math.log(BLUNDER_MIN_MEDIAN), BLUNDER_MIN_SIGMA))
    if p.get(PRODIGY_MAX_KEY):
        bm = min(bm, BLUNDER_PRODIGY_MAX)
    return round(bm, 4)


def _blunder_step(p, age, aging, growth_scale=1.0):
    bm = p[BLUNDER_MIN_KEY]
    if age < READ_DECLINE_AGE:
        learn = BLUNDER_LEARN ** max(0.0, growth_scale)
        p[BLUNDER_KEY] = round(bm + (p[BLUNDER_KEY] - bm) * learn, 4)
    else:
        p[BLUNDER_KEY] = round(min(BLUNDER_CAP, p[BLUNDER_KEY] + BLUNDER_DECLINE * aging), 4)


def ensure_blunder(p, age):
    """ポカ率をまだ持っていない選手に、才能と年齢に応じた値を与える（13歳から規則どおりに進めた値）"""
    if BLUNDER_KEY in p and BLUNDER_MIN_KEY in p:
        return
    p[BLUNDER_MIN_KEY] = _blunder_min_for(p)
    p[BLUNDER_KEY] = round(p[BLUNDER_MIN_KEY] + BLUNDER_BIRTH_EXTRA, 4)
    for a in range(14, (age or 13) + 1):
        _blunder_step(p, a, p.get(AGING_KEY, 1.0))


def _read_delta(age):
    if age < 25:
        return READ_GROWTH_YOUNG
    if age < 30:
        return READ_GROWTH_MID
    if age < READ_DECLINE_AGE:
        return 0.0
    return -READ_DECLINE


def ensure_read(p, age):
    """読みの深さをまだ持っていない選手（この仕組みより前の選手）に、才能と年齢に応じた値を与える
    （13歳でピーク−1から始めて、成長の規則どおりに今の年齢まで進めた値）"""
    if READ_KEY in p and READ_MAX_KEY in p:
        return
    p[READ_MAX_KEY] = _read_max_for(p)
    r = max(READ_FLOOR, p[READ_MAX_KEY] - READ_START_GAP)
    for a in range(14, (age or 13) + 1):
        d = _read_delta(a)
        r = min(p[READ_MAX_KEY], r + d) if d > 0 else max(READ_FLOOR, r + d * p.get(AGING_KEY, 1.0))
    p[READ_KEY] = round(r, 3)


def _age_factor(age):
    """年齢ごとの終盤力の伸び（成長の速さにかける倍率）。25歳まで大きく伸び、30代前半まで少しずつ、
    44歳まで横ばい、45歳から衰える（衰えは成長の速さ×0.75ずつ）。以前は「30歳まで1倍・37歳まで半分・49歳まで横ばい・50歳から衰え」で、
    育ちきるのが遅く、最盛期が30代後半〜40代、Aリーグの平均年齢が45歳前後になっていた（新人がAに上がるまで20季以上）。
    試算（奨励会〜リーグの昇降級まで早回し）で、Aリーグの年齢中央値が約40→37歳、Aに初めて上がるまでが約15→13季に縮んだ"""
    if age < 25:
        return 1.6
    if age < 30:
        return 1.0
    if age < 35:
        return 0.4
    if age < 45:
        return 0.0
    return -ENDGAME_DECLINE


def tone_down_prodigy(p):
    """（移行用）大天才を弱める前に生まれた大天才の能力を、今の上限に合わせる。何度呼んでも同じ結果になる。
    天賦の範囲は26マスまで、2段目は無し、読みの深さのピークは1.4まで、ポカ率の下限は1.2%以上。直したらTrue"""
    if not p.get(PRODIGY_MAX_KEY):
        return False
    before = dict(p)
    p[GENIUS_MAX_KEY] = min(p.get(GENIUS_MAX_KEY, PRODIGY_PEAK), PRODIGY_PEAK)
    p[GENIUS_KEY] = min(p.get(GENIUS_KEY, 0), p[GENIUS_MAX_KEY])
    p[PRODIGY_KEY] = 0
    if READ_MAX_KEY in p:
        p[READ_MAX_KEY] = min(p[READ_MAX_KEY], READ_PRODIGY_MIN)
        if READ_KEY in p:
            p[READ_KEY] = min(p[READ_KEY], p[READ_MAX_KEY])
    if BLUNDER_MIN_KEY in p:
        p[BLUNDER_MIN_KEY] = max(p[BLUNDER_MIN_KEY], BLUNDER_PRODIGY_MAX)
        if BLUNDER_KEY in p:
            p[BLUNDER_KEY] = max(p[BLUNDER_KEY], p[BLUNDER_MIN_KEY])
    return p != before


def _roll_genius():
    """天賦の抽選：まれに大天才（PRODIGY_CHANCE）、それ以外の天賦は従来どおり（GENIUS_CHANCE）"""
    r = random.random()
    if r < PRODIGY_CHANCE:
        return {GENIUS_KEY: 4, GENIUS_MAX_KEY: PRODIGY_PEAK, PRODIGY_KEY: 0, PRODIGY_MAX_KEY: PRODIGY2_PEAK}
    if r < PRODIGY_CHANCE + GENIUS_CHANCE:
        return {GENIUS_KEY: 4, GENIUS_MAX_KEY: random.randint(*GENIUS_PEAK_RANGE)}
    return {}


def birth_skills(master_params=None):
    """新しく生まれる個体の終盤力・定跡を決める（師匠がいれば終盤力を2割、定跡を7割受け継ぐ。定跡は最低16手）"""
    endgame = ENDGAME_BIRTH_MAX * random.betavariate(1.0, ENDGAME_BIRTH_BETA)
    growth = _growth_speed()
    if master_params:
        endgame += ENDGAME_INHERIT * master_params.get(ENDGAME_KEY, 0.0)
    out = {
        ENDGAME_KEY: round(min(ENDGAME_CAP, endgame), 2),
        ENDGAME_GROWTH_KEY: round(growth, 3),
        BOOK_KEY: min(BOOK_CAP, max(BOOK_NEWCOMER_MIN, int(BOOK_INHERIT * (master_params or {}).get(BOOK_KEY, 0)))),
        BOOK_RATE_KEY: _book_rate_for(growth),
        **_roll_genius(),
    }
    out[READ_MAX_KEY] = _read_max_for(out)
    out[READ_KEY] = round(max(READ_FLOOR, out[READ_MAX_KEY] - READ_START_GAP), 3)
    out[BLUNDER_MIN_KEY] = _blunder_min_for(out)
    out[BLUNDER_KEY] = round(out[BLUNDER_MIN_KEY] + BLUNDER_BIRTH_EXTRA, 4)
    return out


def veteran_skills(age):
    """（初期化・移行用）この年齢までキャリアを積んできた個体の終盤力・定跡を、成長の規則どおりに逆算して作る"""
    s = birth_skills()
    endgame = s[ENDGAME_KEY]
    for a in range(20, age):
        endgame = min(ENDGAME_CAP, max(0.0, endgame + s[ENDGAME_GROWTH_KEY] * _age_factor(a + 1)))
    s[ENDGAME_KEY] = round(endgame, 2)
    s[BOOK_KEY] = int(min(BOOK_CAP, s[BOOK_RATE_KEY] * max(0, age - 20)))
    del s[READ_KEY]
    ensure_read(s, age)
    del s[BLUNDER_KEY]
    ensure_blunder(s, age)
    return s


def ensure_skills(ind):
    """終盤力・定跡を持っていない個体（この仕組みの導入前からの個体）に、年齢相応の値を与える"""
    if ENDGAME_KEY not in ind.params:
        ind.params.update(veteran_skills(ind.age))
    ensure_read(ind.params, ind.age)
    ensure_blunder(ind.params, ind.age)


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


def _aging_factor(p):
    """衰えの速さの倍率。まだ決まっていない選手（この仕組みより前の選手・奨励会の子）は、初めて呼ばれたときに決める"""
    if AGING_KEY not in p:
        p[AGING_KEY] = (round(random.uniform(*SLOW_AGING_RANGE), 2)
                        if random.random() < SLOW_AGING_CHANCE else 1.0)
    return p[AGING_KEY]


def season_growth(ind, growth_scale=1.0):
    """季の終わりに、終盤力の成長・衰えと、定跡の習得を反映する（年齢は既に1つ進んでいる前提）。
    growth_scale：終盤力の伸び（成長期のプラス分だけ）にかける倍率。フリークラスで過ごした季は
    FREE_CLASS_GROWTH_SCALE（実戦が少ないので伸びにくい）。衰えには掛けない"""
    p = ind.params
    if ENDGAME_KEY not in p:
        return
    delta = p.get(ENDGAME_GROWTH_KEY, 0.0) * _age_factor(ind.age)
    aging = _aging_factor(p)
    if delta > 0:
        delta *= growth_scale
    else:
        delta *= aging
    p[ENDGAME_KEY] = round(min(ENDGAME_CAP, max(0.0, p[ENDGAME_KEY] + delta)), 2)
    if p.get(GENIUS_MAX_KEY):
        if ind.age < 30:
            grow = PRODIGY_GROWTH if p.get(PRODIGY_MAX_KEY) else GENIUS_GROWTH
            p[GENIUS_KEY] = min(p[GENIUS_MAX_KEY], p.get(GENIUS_KEY, 0) + grow)
        elif ind.age >= GENIUS_DECLINE_AGE:
            p[GENIUS_KEY] = max(0, round(p.get(GENIUS_KEY, 0) - GENIUS_DECLINE * aging, 2))
    # 読みの深さ：まだ持っていなければ年齢相応の値を与え、以降は1季ずつ伸び・衰える
    if READ_KEY not in p or READ_MAX_KEY not in p:
        ensure_read(p, ind.age)
    else:
        d = _read_delta(ind.age)
        if d > 0:
            p[READ_KEY] = round(min(p[READ_MAX_KEY], p[READ_KEY] + d * growth_scale), 3)
        elif d < 0:
            p[READ_KEY] = round(max(READ_FLOOR, p[READ_KEY] + d * aging), 3)
    if BLUNDER_KEY not in p or BLUNDER_MIN_KEY not in p:
        ensure_blunder(p, ind.age)
    else:
        _blunder_step(p, ind.age, aging, growth_scale)
    young = BOOK_YOUNG_SPEEDUP if ind.age < BOOK_YOUNG_AGE else 1.0
    p[BOOK_KEY] = int(min(BOOK_CAP, p.get(BOOK_KEY, 0) + p.get(BOOK_RATE_KEY, 0.0) * young + random.random()))
