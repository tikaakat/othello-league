import random


class LeagueIndividual:
    """
    リーグ制における個体。
    通常のパラメータ・血統情報に加えて、所属リーグ・在籍シーズン数・道場情報を持つ。
    """
    def __init__(self, ind_id, league, params=None, dojo=None, generation=0,
                 parent_a_id=None, parent_b_id=None, display_name=None, initial_age=None):
        self.id = ind_id
        self.league = league              # 'A' / 'B' / 'C' / 'D'
        self.params = params or {}
        self.dojo = dojo                  # 所属道場（8大流派名 or None＝無流派）
        self.buff_multiplier = None       # 道場バフの倍率（道場所属時のみ使用）
        self.display_name = display_name  # 人名（例：「佐藤2」）
        self.awakened_param = None        # 覚醒で突破したパラメータ名（あれば）
        self.black_count = 0              # 通算で黒番を持った回数（先後を均等にするための管理用。シーズンをまたいで累積）
        self.white_count = 0               # 通算で白番を持った回数
        self.generation = generation
        self.parent_a_id = parent_a_id
        self.parent_b_id = parent_b_id
        self.initial_age = initial_age if initial_age is not None else random.randint(18, 24)
        self.age_multipliers = {}              # 年齢バフ（パラメータ別倍率）。シーズン開始時に再抽選
        self.consecutive_losing_seasons = 0     # Dリーグの2連続負け越し引退判定用

        self.elo = 1500.0
        self.peak_elo = 1500.0  # 歴代最高Elo（殿堂ページの表示用）
        self.volatility = 1.0  # ムラ気（隠しパラメータ）。評価ノイズの倍率。基準1.0、高いほど結果が大きく振れる
        self.seasons_in_league = 0        # 現在のリーグに在籍しているシーズン数
        self.total_seasons = 0            # 通算在籍シーズン数（引退判定用）
        self.retired = False
        self.match_history = []

        # このシーズンの成績（league.pyの引退判定用に、シーズンごとに上書きされる一時的な値。
        # 保存は不要＝to_dict/from_dictには含めない）
        self.win_this_season = 0
        self.loss_this_season = 0

    @property
    def age(self):
        return self.initial_age + self.total_seasons

    def to_dict(self):
        return {
            "id": self.id,
            "league": self.league,
            "params": self.params,
            "dojo": self.dojo,
            "buff_multiplier": self.buff_multiplier,
            "display_name": self.display_name,
            "awakened_param": self.awakened_param,
            "black_count": self.black_count,
            "white_count": self.white_count,
            "generation": self.generation,
            "parent_a_id": self.parent_a_id,
            "parent_b_id": self.parent_b_id,
            "initial_age": self.initial_age,
            "age_multipliers": self.age_multipliers,
            "consecutive_losing_seasons": self.consecutive_losing_seasons,
            "elo": self.elo,
            "peak_elo": self.peak_elo,
            "volatility": self.volatility,
            "seasons_in_league": self.seasons_in_league,
            "total_seasons": self.total_seasons,
            "retired": self.retired,
            "match_history": self.match_history,
        }

    @staticmethod
    def from_dict(d):
        ind = LeagueIndividual(
            d["id"], d["league"], d.get("params"), d.get("dojo"),
            d.get("generation", 0), d.get("parent_a_id"), d.get("parent_b_id"),
            display_name=d.get("display_name"),
        )
        ind.initial_age = d.get("initial_age", random.randint(18, 24))  # 既存個体は移行時のみランダム付与
        ind.age_multipliers = d.get("age_multipliers", {})
        ind.consecutive_losing_seasons = d.get("consecutive_losing_seasons", 0)
        ind.elo = d.get("elo", 1500.0)
        ind.peak_elo = d.get("peak_elo", ind.elo)
        ind.volatility = d.get("volatility", 1.0)
        ind.buff_multiplier = d.get("buff_multiplier")
        ind.awakened_param = d.get("awakened_param")
        ind.black_count = d.get("black_count", 0)
        ind.white_count = d.get("white_count", 0)
        ind.seasons_in_league = d.get("seasons_in_league", 0)
        ind.total_seasons = d.get("total_seasons", 0)
        ind.retired = d.get("retired", False)
        ind.match_history = d.get("match_history", [])
        return ind
