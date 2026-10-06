def k_factor(total_seasons):
    """
    経験の浅い個体ほどレートの変動幅を大きくし、実力相応の値に早く収束させる。
    将棋界等での「新人はレート変動が大きい」運用に倣った、段階的な逓減方式。

    数値（18/13/10）は、過去34季分の実際の対局結果（約9200局）を使い、
    「Eloの示す期待勝率」と「実際の勝率」のズレ（Brierスコア）を最小化する形で
    検証・調整した値。旧数値（64/48/32）は期待勝率を実際より極端に見せていた
    （例：Elo差から期待勝率75%と出る対戦の実際の勝率は約62%程度しかなかった）。
    これは対局1局ごとの評価ノイズ（engine.pyの±10×ムラ気）による実力差以外の
    ブレをKが過大評価し、レートを実際の実力差以上に動かしすぎていたため。
    Kを定数（K=10前後）に固定するだけでも大きく改善するが、新人のレートが
    初期値1500から実力相応の値に収束する速さも多少は保ちたいため、段階的な
    逓減自体は維持し、数値だけ検証結果に合わせて下げた。
    """
    if total_seasons < 3:
        return 18
    elif total_seasons < 8:
        return 13
    return 10


def update_elo(rating_a, rating_b, outcome_a, k=None, total_seasons_a=None, total_seasons_b=None):
    """
    outcome_a: 'win' / 'loss' / 'draw'（Aから見た結果）
    kを明示的に指定した場合はそれを両者に一律適用する（後方互換のため）。
    指定しない場合、total_seasons_a/bからそれぞれの経験に応じたK値を個別に算出する
    （新人ほど大きく動き、ベテランほど安定するように）。
    """
    score_a = {"win": 1.0, "draw": 0.5, "loss": 0.0}[outcome_a]
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    expected_b = 1 - expected_a
    score_b = 1.0 - score_a

    k_a = k if k is not None else k_factor(total_seasons_a if total_seasons_a is not None else 999)
    k_b = k if k is not None else k_factor(total_seasons_b if total_seasons_b is not None else 999)

    new_rating_a = rating_a + k_a * (score_a - expected_a)
    new_rating_b = rating_b + k_b * (score_b - expected_b)

    return new_rating_a, new_rating_b
