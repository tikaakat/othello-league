import random


class LeagueIndividual:
    """
    リーグ制における個体。
    通常のパラメータ・血統情報に加えて、所属リーグ・在籍シーズン数・一門情報を持つ。
    """
    def __init__(self, ind_id, league, params=None, generation=0,
                 parent_a_id=None, parent_b_id=None, display_name=None, initial_age=None,
                 clan_root_id=None):
        self.id = ind_id
        self.league = league              # 'A' / 'B' / 'C' / 'D'
        self.params = params or {}
        # 一門の開祖ID。通常は師匠のclan_root_idをそのまま継承するが、
        # 稀に本人がここで新しい一門の開祖になる（分岐）。未指定時は自分自身が開祖（＝新規開祖）
        self.clan_root_id = clan_root_id if clan_root_id is not None else ind_id
        self.display_name = display_name  # 人名（例：「佐藤2」）
        self.awakened_param = None        # 覚醒で突破したパラメータ名（あれば）
        self.black_count = 0              # 通算で黒番を持った回数（先後を均等にするための管理用。シーズンをまたいで累積）
        self.white_count = 0               # 通算で白番を持った回数
        self.generation = generation
        self.parent_a_id = parent_a_id
        self.parent_b_id = parent_b_id
        self.initial_age = initial_age if initial_age is not None else random.randint(18, 24)
        self.age_multipliers = {}              # 年齢バフ（パラメータ別倍率）。シーズン開始時に再抽選
        self.demotion_points = 0                # Dリーグの降級点（2点で引退。連続でなくてもよい）

        self.peak_elo = 1500.0  # 歴代最高Elo（殿堂ページの表示用）。elo setterが自動更新する
        self.elo = 1500.0
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

    @property
    def elo(self):
        return self._elo

    @elo.setter
    def elo(self, value):
        # 歴代最高Elo（peak_elo）を、シーズン終了時の値だけでなく対局のたびに追跡する。
        # 1シーズン中に総当たり/スイス方式・タイトル戦（予選ブラケット・挑戦者決定戦等）と
        # 何度もeloが上下するため、シーズン終了時点の値だけを見ていると、シーズン中に
        # 一時的に到達した最高値（対局ログ上で表示される値）を取り逃してしまう
        # （殿堂・個体詳細ページの「最高Elo」が対局ログ上の値より低く見える不具合の原因だった）
        self._elo = value
        if value > self.peak_elo:
            self.peak_elo = value

    def to_dict(self):
        return {
            "id": self.id,
            "league": self.league,
            "params": self.params,
            "clan_root_id": self.clan_root_id,
            "display_name": self.display_name,
            "awakened_param": self.awakened_param,
            "black_count": self.black_count,
            "white_count": self.white_count,
            "generation": self.generation,
            "parent_a_id": self.parent_a_id,
            "parent_b_id": self.parent_b_id,
            "initial_age": self.initial_age,
            "age_multipliers": self.age_multipliers,
            "demotion_points": self.demotion_points,
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
            d["id"], d["league"], d.get("params"),
            d.get("generation", 0), d.get("parent_a_id"), d.get("parent_b_id"),
            display_name=d.get("display_name"),
            # 一門制導入前の既存個体はclan_root_idを持たないため、移行時のみ自分自身を開祖として扱う
            # （過去の血統を遡っての一門再構築はしない、という割り切り）
            clan_root_id=d.get("clan_root_id"),
        )
        ind.initial_age = d.get("initial_age", random.randint(18, 24))  # 既存個体は移行時のみランダム付与
        ind.age_multipliers = d.get("age_multipliers", {})
        # 旧フィールド名（consecutive_losing_seasons）からの1回限りの移行。
        # 旧方式は「2連続負け越し」で、新方式は「負け越し2回（連続でなくてもよい）」のため
        # 厳密には意味が異なるが、既存の値をそのまま降級点の初期値として引き継ぐ
        # （移行時点で急に引退・保護が変わらないよう、素直に数値を転用する）
        ind.demotion_points = d.get("demotion_points", d.get("consecutive_losing_seasons", 0))
        # peak_elo→eloの順で設定する（eloのsetterがpeak_eloとの比較で自動更新するため、
        # 先にpeak_eloを正しい保存値にしておかないと、ロード直後に誤って上書きされてしまう）
        ind.peak_elo = d.get("peak_elo", d.get("elo", 1500.0))
        ind.elo = d.get("elo", 1500.0)
        ind.volatility = d.get("volatility", 1.0)
        ind.awakened_param = d.get("awakened_param")
        ind.black_count = d.get("black_count", 0)
        ind.white_count = d.get("white_count", 0)
        ind.seasons_in_league = d.get("seasons_in_league", 0)
        ind.total_seasons = d.get("total_seasons", 0)
        ind.retired = d.get("retired", False)
        ind.match_history = d.get("match_history", [])
        return ind
