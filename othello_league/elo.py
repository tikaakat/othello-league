def update_elo(rating_a, rating_b, outcome_a, k=32):
    """outcome_a: 'win' / 'loss' / 'draw'（Aから見た結果）"""
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    score_a = {"win": 1.0, "draw": 0.5, "loss": 0.0}[outcome_a]
    new_rating_a = rating_a + k * (score_a - expected_a)

    expected_b = 1 - expected_a
    score_b = 1.0 - score_a
    new_rating_b = rating_b + k * (score_b - expected_b)

    return new_rating_a, new_rating_b
