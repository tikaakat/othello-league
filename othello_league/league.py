import random

from .individual import LeagueIndividual
from .buffs import maybe_awaken
from .skills import birth_skills
from .names import NameRegistry, infer_gender

# リーグの定員（Dリーグは「定員」ではなく、強制引退を起こさないための大きめのバッファ。
# 下記D_NEWCOMER_INTAKEによる固定枠の受け入れを人数に関わらず続けられるよう、
# 実際の在籍者数より十分大きい値にしてある）
LEAGUE_CAPACITY = {"A": 9, "B": 12, "C": 16, "D": 60}

# 昇降格の定員設定
A_TO_B_RELEGATE = 2  # A→B降格人数（＝B→A昇格人数）
B_TO_C_RELEGATE = 3  # B→C降格人数（＝C→B昇格人数）
D_TO_C_PROMOTE = 3   # D→C昇格人数（C→D降格人数と揃え、Cリーグの定員超過を防ぐ）

RETIREMENT_AGE = 60
RETIREMENT_AGE_HARD_CAP = 65  # 引退を免除されていても、この年齢でタイトル保持中でなければ引退する
D_DEMOTION_POINT_LIMIT = 3  # Dリーグでの降級点がこの数に達するとフリークラスへ（連続でなくてもよい）
# フリークラス（将棋の順位戦のフリークラスに倣う）：Dで降級点が3点に達した選手はDリーグ（青龍戦リーグ）から外れ、
# タイトル戦（予選を含む）にだけ出場する。将棋の「参加棋戦数＋8勝以上、かつ勝率6割以上」に倣い、1季のタイトル戦で
# 「出場した棋戦数＋FREE_RETURN_EXTRA_WINS」勝以上、かつ勝率 FREE_RETURN_RATE 以上ならDへ復帰。
# 将棋では陥落した棋士の復帰はごくまれなため、それに近づくよう厳しめにしている（＋2・6割では4季で6人が復帰していた。
# ＋4・6割5分を当てはめると同じ4季で1人）。タイトル戦に挑戦した場合も復帰。在籍 FREE_CLASS_SEASON_LIMIT 季で
# 復帰できなければ引退。以前は降級点3点で即引退で、若手を守るためにデビュー3季は降級点を付けていなかったが、
# 落ちても引退ではなくなったので、その猶予はやめた
FREE_CLASS = "F"
FREE_RETURN_EXTRA_WINS = 4
FREE_RETURN_RATE = 0.65
FREE_CLASS_SEASON_LIMIT = 10
D_DEMOTION_POINT_FLOOR = 1  # 勝ち越し・救済で減らせる下限。1点目はCへ昇級するまで消えない
C_TO_D_RELEGATE = 3  # C→D降格人数（B→Cと同数）

# Dリーグの新人受け入れ枠（新人リーグの募集人数）。
# 以前は「定員に対する欠員数」から算出しており、在籍者が増えて欠員が無くなると
# 既存メンバーをElo下位から強制引退させてまで枠を確保していた。これだと一季の間に
# まとまった人数が不自然に引退してしまう上、在籍者数が増え続けると引退者も増え続ける
# 歪な構造になるため、在籍者数に関わらず毎季必ずこの人数だけを固定で受け入れる方式に変更した
# （在籍者数は増減してよく、それ自体で強制引退を発生させることはない）
# Dリーグが増え続けている（引退が降級点によるものだけになったため）ことへの対策として一度2名に
# 減らしたが、いきなり変えるのは避けることにして3名に戻した（人数の推移は引き続き要観察）
D_NEWCOMER_INTAKE = 3
D_INITIAL_ROSTER_SIZE = 16  # ブートストラップ（初年度）時点でのDリーグ初期人数

PARAM_KEYS = [
    "corner_weight", "danger_zone_weight", "mobility_weight", "edge_stability_weight",
    "frontier_weight", "disc_weight", "parity_weight", "center_weight",
]


def free_class_returns(records):
    """フリークラスの復帰判定。records：{ID: {"win", "loss", "entries"（出場した棋戦の数）, "challenged"（タイトル戦に挑戦したか）}}"""
    out = set()
    for iid, r in records.items():
        w, l = r["win"], r["loss"]
        if r.get("challenged") or (w >= r["entries"] + FREE_RETURN_EXTRA_WINS and w / max(1, w + l) >= FREE_RETURN_RATE):
            out.add(iid)
    return out


def relegate_and_retire(rosters, season, titleholders=None, suzaku_league_ids=None,
                         extra_protected_ids=None, demotion_relief_ids=None, free_return_ids=None):
    """
    1シーズンの対局終了後の昇降格・強制引退の判定のみを行う（Dリーグの新規補充は
    recruit_d_league()が別関数として、次シーズンの対局が始まる前に行う）。

    以前はこの関数の中でDリーグの新規補充（新人リーグの勝者・自動生成の新弟子）まで
    一括で行っており、その季の対局後に新弟子が参入する形だった。
    補充処理を次シーズン開始前（対局前）に切り出すことで、新人リーグの勝者は
    新人リーグが対象とした季からそのまま出走できるようになる
    （募集人数自体はD_NEWCOMER_INTAKEの固定値で、在籍者数や欠員には左右されない）。

    suzaku_league_idsが与えられた場合、朱雀紅白リーグに在籍中の個体は、タイトル保持者と
    同様に強制引退（年齢・Dリーグ降級点）の対象から除外する。
    予選を勝ち抜いて来季から紅白リーグに加入する個体も、その加入前の季にこれらの条件で
    引退させてしまうと来季の紅白リーグに欠員が生じるため、同様に保護する
    （C〜Aリーグの定員超過による「降格」は引退ではなく在籍リーグが変わるだけなので対象外）。

    extra_protected_idsが与えられた場合も同様に保護する。この季の各タイトル戦の
    挑戦者（青龍・朱雀・白虎・玄武、最大4名）が対象。挑戦するところまで勝ち上がったのに、
    同じ季のうちに引退させてしまうのを防ぐため（勝って在位者になればtitleholder_idsの
    保護に引き継がれる）。

    demotion_relief_idsが与えられた場合、Dリーグ降級点の判定（下記）で対象者の降級点を
    1点減らす（0未満にはしない）。この季の白虎トーナメント出場者（16名）・玄武ブロック
    優勝者（4名）が対象。降級点による引退そのものは免除しない（減点のみ）。
    年齢引退も免除しない（以前は免除していたが、高齢の選手が残り続けるため改めた）。

    戻り値: (更新後のrosters dict, 引退者リスト, 来季のDリーグ新人受け入れ枠数
             （D_NEWCOMER_INTAKEの固定値）, 降級点イベント（individual_id => "gained"/"cleared"）)
    """
    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])
    F = list(rosters.get(FREE_CLASS, []))
    d_start_points = {ind.id: ind.demotion_points for ind in D}

    # --- タイトル保持者・朱雀紅白リーグ在籍者（来季から加入予定の予選通過者も含む）・
    #     この季のタイトル戦挑戦者（extra_protected_ids）は、年齢引退とDリーグ降級点
    #     による引退の両方を免除する（年齢引退のみの免除対象は下の age_exempt_ids を参照） ---
    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    protected_ids = set(titleholder_ids)
    if suzaku_league_ids:
        protected_ids.update(suzaku_league_ids)
    if extra_protected_ids:
        protected_ids.update(extra_protected_ids)

    # --- 昇降格の対象は、年齢引退を除外する「前」の今季順位で決める。
    #     除外後のリストで A[-2:] のように切ると、最下位が年齢引退した場合に
    #     本来残留のはずの個体が繰り下がって降格してしまうため。
    #     年齢引退した個体はその枠ごと抜け、空いた枠は後段の_backfillで下位リーグから埋める ---
    ab_n = min(A_TO_B_RELEGATE, len(A), len(B))
    bc_n = min(B_TO_C_RELEGATE, len(B), len(C))
    cd_n = min(C_TO_D_RELEGATE, len(C))
    dc_n = min(D_TO_C_PROMOTE, len(D))

    a_relegate = A[len(A) - ab_n:]
    a_remain = A[:len(A) - ab_n]
    b_promote_to_a = B[:ab_n]
    b_relegate_to_c = B[len(B) - bc_n:]
    b_remain = B[ab_n:len(B) - bc_n]
    c_promote_to_b = C[:bc_n]
    c_relegate_to_d = C[len(C) - cd_n:]
    c_remain = C[bc_n:len(C) - cd_n]
    d_promote_to_c = D[:dc_n]
    d_remain = D[dc_n:]

    # --- 年齢引退の免除は、タイトル保持者・朱雀紅白リーグ在籍者・この季の挑戦者（protected_ids）に限る。
    #     以前はAリーグ在籍者・Aへの昇格者・白虎トーナメント出場者・玄武ブロック優勝者も免除しており、
    #     実績のある高齢の選手が60歳を過ぎてもAリーグに残り続けていた（Aの9人中5人が60歳以上の季もあった）。
    #     さらに、免除されていても RETIREMENT_AGE_HARD_CAP（65歳）に達したら、タイトル保持中でない限り引退する ---
    age_exempt_ids = set(protected_ids)

    age_retired = []
    aged_out_ids = set()
    for ind in A + B + C + D + F:
        exempt = ind.id in age_exempt_ids if ind.age < RETIREMENT_AGE_HARD_CAP else ind.id in titleholder_ids
        if ind.age >= RETIREMENT_AGE and not exempt:
            ind.retired = True
            ind.retirement_reason = f"{RETIREMENT_AGE}歳の年齢規定により引退"
            # 引退する個体も今季は実際に対局しているので、在籍シーズン数に数える
            ind.total_seasons += 1
            age_retired.append(ind)
            aged_out_ids.add(ind.id)

    def _drop_aged(members):
        return [ind for ind in members if ind.id not in aged_out_ids]

    a_relegate, a_remain = _drop_aged(a_relegate), _drop_aged(a_remain)
    b_promote_to_a, b_relegate_to_c, b_remain = _drop_aged(b_promote_to_a), _drop_aged(b_relegate_to_c), _drop_aged(b_remain)
    c_promote_to_b, c_relegate_to_d, c_remain = _drop_aged(c_promote_to_b), _drop_aged(c_relegate_to_d), _drop_aged(c_remain)
    d_promote_to_c, d_remain = _drop_aged(d_promote_to_c), _drop_aged(d_remain)

    # 降級点の増減（結果タブへの「+点1」「-点1」「消」表示用）。
    # individual_id => "gained"（青龍戦リーグで負け越し、+1）/ "relief"（玄武ブロック優勝等で-1）/
    # "gained_relief"（同じ季に両方発生。相殺されて見た目の点数が変わらないこともある）/
    # "cleared"（0に戻った。Dを卒業した際のリセット・減点が貯まり分を相殺しきった場合のいずれでも）
    demotion_events = {}

    # Dリーグを卒業（Cへ昇格）する個体は、降級点をリセットする。
    # 降級点はDリーグ在籍中の成績のみを反映すべきものなので、
    # リセットしないと「昔Dにいた時の降級点」が残ったまま何季も引き継がれ、
    # 何季も後にDへ舞い戻った際に、既に引退間際の状態で再出発することになってしまう。
    for ind in d_promote_to_c:
        if ind.demotion_points > 0:
            # 結果タブで「それまで何点あったか」を取り消し線付きで表示できるよう、
            # クリア直前の点数をイベント文字列に埋め込む（例: "cleared1"）
            demotion_events[ind.id] = f"cleared{ind.demotion_points}"
        ind.demotion_points = 0

    A = a_remain + b_promote_to_a
    B = b_remain + a_relegate + c_promote_to_b
    C = c_remain + b_relegate_to_c + d_promote_to_c

    # --- Cリーグが定員超過した場合はDへ降格させる（引退ではない。
    #     A〜Cリーグの引退条件は年齢のみとする方針のため、Elo下位を退場させるのではなく
    #     降格として扱う。本来D_TO_C_PROMOTEとC_TO_D_RELEGATEを揃えていれば
    #     超過しないはずだが、初期ロスターが定員通りでない場合などへの保険） ---
    def _relegate_overflow(members, capacity):
        if len(members) <= capacity:
            return members, []
        protected = [ind for ind in members if ind.id in titleholder_ids]
        sorted_members = sorted(
            (ind for ind in members if ind.id not in titleholder_ids),
            key=lambda ind: ind.elo, reverse=True,
        )
        shortage = len(members) - capacity
        keep = sorted_members[:max(0, len(sorted_members) - shortage)]
        overflow = sorted_members[max(0, len(sorted_members) - shortage):]
        return protected + keep, overflow

    C, c_relegate_overflow = _relegate_overflow(C, LEAGUE_CAPACITY["C"])

    # --- Dリーグ：降級点制。負け越した季ごとに降級点が1つ積み重なり（連続していなくてもよい）、
    #     D_DEMOTION_POINT_LIMITに達したら実力・在籍年数に関わらず即引退する（ただしタイトル
    #     保持者・朱雀紅白リーグ在籍者・extra_protected_idsは、age_retiredと同様に猶予対象）。
    #     以前は「2季連続で負け越した場合のみ」引退としていたが、勝ち越しを挟むと
    #     カウンタがリセットされてしまい、長期的に負け越しがちな個体がいつまでも
    #     居座れる不備があったため、点として積み重ねる方式に変更した。
    #     ただし積み上がる一方では救いが無さすぎるため、勝ち越した季・
    #     demotion_relief_ids該当（白虎トーナメント出場・玄武ブロック優勝）の季は
    #     それぞれ1点減らす（複数該当すれば重複して減らしてよい）。
    #     ただし消せるのは2点目以降のみで、1点目はCへ昇級するまで消えない（将棋のC級2組の
    #     降級点に倣う）。以前は0点まで戻せたため、負け越しと勝ち越しを繰り返して1点を
    #     行き来しながらDに長く居座る個体が多く、Dリーグの人数が膨らんでいた
    #     （過去41季の結果で当てはめると、Dの人数は1季あたり3〜4人減る見込み）。
    #     同じ季に負け越しと救済（relief）が重なった場合も、負け越しの1点目は付く（相殺しない）。
    #     この判定は「今季も引き続きDに在籍していた個体（d_remain）」のみを対象にする。
    #     今季Cから降格してきた個体（c_relegate_to_d・c_relegate_overflow）は、
    #     まだDでの対局実績が無い（直前の成績はC所属時のもの）ため対象外とし、
    #     降級点を0にリセットして「Dでの降級点」を来季以降ゼロから数え直す。
    #
    #     protected_ids（タイトル保持者・朱雀紅白リーグ在籍者・この季の挑戦者）は
    #     強制引退そのものは免除されるが、以前はその間も負け越すたびに降級点が
    #     上限なく積み上がり続けてしまい、(a) 白虎トーナメント出場者等
    #     （demotion_relief_idsのみ対象で-1点止まり）と比べて不公平、
    #     (b) 保護が外れた季に「点3」のような想定外の点数から再出発する、という
    #     2つの問題があった。これを解消するため、protected_idsもdemotion_relief_idsと
    #     同じ「-1点」救済の対象に合流させる（＝保護中は2点目以降が増えない。1点目は付く）。これにより白虎出場者と保持者・挑戦者は同じルールで扱われ、
    #     保護が外れた時点の点数も従来の最大値（D_DEMOTION_POINT_LIMIT）を超えない。
    #     さらに保険として、計算結果そのものもD_DEMOTION_POINT_LIMITで上限キャップし、
    #     どのような経路でも「点3」以上の表示が出ないようにする。 ---
    demotion_relief_ids = set(demotion_relief_ids or set()) | protected_ids
    d_up_or_out_retired = []
    d_keep = []
    d_to_free = []
    for ind in d_remain:
        before = ind.demotion_points
        # 「青龍戦リーグ（D所属個体のA〜D総当たり・スイス方式本戦）」の勝敗のみで+1/-1を
        # 判定する（win_this_season/loss_this_seasonはタイトル戦の前にA〜Dの結果だけで
        # セットされるため、玄武予選・玄武本戦等のタイトル戦の勝敗はここに混ざらない）
        lost_more = ind.loss_this_season > ind.win_this_season
        won_more = ind.win_this_season > ind.loss_this_season
        relief = ind.id in demotion_relief_ids
        # 負け越したら必ず1点付く（救済と同じ季でも相殺しない）。そのうえで勝ち越し・救済で減らせるのは
        # 2点目以降のみ（1点以上持っていれば1点は残る）。以前は同じ季の負け越しと救済を相殺していたため、
        # 紅白リーグ在籍などで保護されている選手は負け越しても1点目が付かなかった（例：第49季の秋山翔）
        points = before + (1 if lost_more else 0)
        reductions = (1 if won_more else 0) + (1 if relief else 0)
        reduced_to = max(min(points, D_DEMOTION_POINT_FLOOR), points - reductions)
        ind.demotion_points = min(reduced_to, D_DEMOTION_POINT_LIMIT)
        # 結果タブの「-点1」表示用：勝ち越し・救済で実際に点が減ったか
        won_reduced = won_more and reduced_to < points
        relief_applied = relief and reduced_to < points - (1 if won_reduced else 0)
        # gained（青龍戦リーグで負け越して+1）・relief（玄武ブロック優勝等で-1）は、
        # 同じ季に両方起きて相殺され見た目の点数が変わらないこともあるため、
        # 片方だけでなく両方が起きたことを別々に結果タブへ出せるよう、
        # "gained_relief"のように複合で記録する（0に戻った場合はclearedを優先する）
        if ind.demotion_points == 0 and before > 0:
            # クリア直前の点数を埋め込む（例: "cleared1"）。結果タブで取り消し線付きの
            # 「点N」として表示するために使う
            demotion_events[ind.id] = f"cleared{before}"
        else:
            parts = []
            if lost_more:
                # 既に降級点を持っていた場合（before > 0）は、その「持っていた点数」も
                # 結果タブで分かるように埋め込む（例: "gained1"）。引退（降級点が上限に
                # 達した）場合も、以前は「+点1」の表示自体を省いていたが、「点1を
                # 持っていたところへ+1点で引退」という経緯が分かるよう、省略をやめて
                # 常に表示するようにした
                parts.append(f"gained{before}")
            # 勝ち越して-1点（0点まで減らなかった場合）。0点まで減った場合は上のclearedで表示される。
            # これが無いと、勝ち越しで減ったのに結果タブに何も表示されず、点数だけが黙って減って見える
            if won_reduced:
                parts.append(f"won{before}")
            # relief単独（今季負け越していない）で既に0点だった場合は、実質的に
            # 何も変化していないため表示しない（relieveする対象の点が無かった）。
            # gainedと同様、結果タブのハイライト表示用に「持っていた点数」を埋め込む
            if relief_applied:
                parts.append(f"relief{before}")
            if parts:
                demotion_events[ind.id] = "_".join(parts)
        if ind.demotion_points >= D_DEMOTION_POINT_LIMIT and ind.id not in protected_ids:
            # 降級点3点：フリークラスへ（引退ではない）。降級点はDに戻ったら0から数え直す
            ind.demotion_points = 0
            d_to_free.append(ind)
        else:
            d_keep.append(ind)

    new_d_arrivals = c_relegate_to_d + c_relegate_overflow
    for ind in new_d_arrivals:
        ind.demotion_points = 0

    # --- フリークラス：復帰条件を満たした選手はDの最後尾へ。満たせないまま在籍
    #     FREE_CLASS_SEASON_LIMIT 季（今季を含む）に達したら引退（タイトル保持者などは免除） ---
    free_return_ids = set(free_return_ids or ())
    f_return, f_stay = [], []
    for ind in F:
        if ind.id in aged_out_ids:
            continue
        if ind.id in free_return_ids:
            ind.demotion_points = 0
            f_return.append(ind)
        elif ind.seasons_in_league + 1 >= FREE_CLASS_SEASON_LIMIT and ind.id not in protected_ids:
            ind.retired = True
            ind.retirement_reason = f"フリークラス在籍{FREE_CLASS_SEASON_LIMIT}季で復帰できず引退"
            ind.total_seasons += 1
            d_up_or_out_retired.append(ind)
        else:
            f_stay.append(ind)
    F = f_stay + d_to_free
    D = d_keep + new_d_arrivals + f_return

    # --- 年齢引退等でA・B・Cに定員割れが生じた場合、下位リーグから繰り上げて埋める。
    #     今季そのリーグに在籍して実際に対局した個体（remain_ids）の中から、今季の順位が
    #     高い順（＝remain_idsの並び順。b_remain/c_remain/d_keepは元々ランク順のため）に
    #     優先して繰り上げる。今季ちょうど1つ下のリーグから昇格/降格してきたばかりの個体は、
    #     今季そのリーグでの対局実績が無いため対象外とし、それでも枠が埋まらない場合に限り
    #     Elo上位から補う（滅多に起きない保険的な扱い）。
    #     exclude_idsは、今季ちょうど1つ下のリーグから昇格してきたばかりの個体を保険的な
    #     Elo補充からも除外する。除外しないと「今季C→B昇格 かつ タイトル獲得でEloが急騰」の
    #     ような個体が、同じ季のうちにB→Aへもバックフィルされ、CからAへ一気に飛び級してしまう ---
    def _backfill(upper, lower, capacity, remain_ids, exclude_ids=frozenset()):
        shortage = capacity - len(upper)
        if shortage <= 0 or not lower:
            return upper, lower
        remain_pool = [ind for ind in lower if ind.id in remain_ids and ind.id not in exclude_ids]
        take = remain_pool[:shortage]
        still_short = shortage - len(take)
        if still_short > 0:
            taken_ids = {ind.id for ind in take}
            rest_pool = [ind for ind in lower if ind.id not in remain_ids and ind.id not in exclude_ids
                         and ind.id not in taken_ids]
            rest_sorted = sorted(rest_pool, key=lambda ind: ind.elo, reverse=True)
            take = take + rest_sorted[:still_short]
        take_ids = {ind.id for ind in take}
        lower_remaining = [ind for ind in lower if ind.id not in take_ids]
        return upper + take, lower_remaining

    c_promote_to_b_ids = {ind.id for ind in c_promote_to_b}
    d_promote_to_c_ids = {ind.id for ind in d_promote_to_c}
    b_remain_ids = {ind.id for ind in b_remain}
    c_remain_ids = {ind.id for ind in c_remain}
    d_keep_ids = {ind.id for ind in d_keep}
    # 今季C→Dへ降格した個体（通常降格・定員超過による追加降格の両方）は、
    # C,D backfillのexclude_idsからも除外する。除外しないと、Cが（age_retired等で）
    # 定員割れした際にこのバックフィルで同じ季のうちにCへ舞い戻ってしまい、
    # 降格がその場で取り消されたのと同じ結果になってしまう
    c_relegate_to_d_ids = {ind.id for ind in c_relegate_to_d} | {ind.id for ind in c_relegate_overflow}

    A, B = _backfill(A, B, LEAGUE_CAPACITY["A"], remain_ids=b_remain_ids, exclude_ids=c_promote_to_b_ids)
    B, C = _backfill(B, C, LEAGUE_CAPACITY["B"], remain_ids=c_remain_ids, exclude_ids=d_promote_to_c_ids)
    C, D = _backfill(C, D, LEAGUE_CAPACITY["C"], remain_ids=d_keep_ids, exclude_ids=c_relegate_to_d_ids)

    # 繰り上げ（backfill）でDからCへ上がった個体も、通常のD→C昇格（d_promote_to_c）と同じく
    # 降級点をリセットする。降級点はDリーグ在籍中の成績のみを反映するもので、Dを卒業したら消える。
    # 結果タブには季開始時点の点数を「cleared{N}」として記録する（今季の増減の記録は上書きする）
    for ind in C:
        if ind.id in d_keep_ids:
            start_points = d_start_points.get(ind.id, 0)
            ind.demotion_points = 0
            if start_points > 0:
                demotion_events[ind.id] = f"cleared{start_points}"
            else:
                demotion_events.pop(ind.id, None)

    # リーグ所属情報・在籍年数の更新
    all_retired = age_retired + d_up_or_out_retired

    for league_name, members in (("A", A), ("B", B), ("C", C), ("D", D), (FREE_CLASS, F)):
        for ind in members:
            if ind.league != league_name:
                ind.league = league_name
                ind.seasons_in_league = 0
            else:
                ind.seasons_in_league += 1
            ind.total_seasons += 1

    # Dリーグの新人受け入れ枠は在籍者数や定員に関わらず毎季固定（D_NEWCOMER_INTAKE）。
    # 以前はここで在籍者数が定員（旧20名）に近づくとElo下位を強制引退させて枠を
    # 確保していたが、一季でまとまった引退者が出てしまう・在籍者が増えるほど
    # 引退者も増えるという歪みがあったため撤廃した。在籍者数は自然な引退
    # （年齢・降級点up-or-out）の分だけ増減し、それ以上の強制調整は行わない
    new_recruit_slots = D_NEWCOMER_INTAKE

    new_rosters = {"A": A, "B": B, "C": C, "D": D, FREE_CLASS: F}
    return new_rosters, all_retired, new_recruit_slots, demotion_events


def recruit_d_league(rosters, season, name_registry=None, pending_characters=None, titleholders=None,
                      suzaku_league_ids=None, auto_fill_vacancy=True):
    """
    Dリーグの新人受け入れ（D_NEWCOMER_INTAKEの固定枠数）を、次シーズンの対局が
    始まる前に行う。まずキャラクリエイト・新人リーグ経由のリクエスト
    （pending_characters）を優先的に参入させ、残り枠のみ通常の新弟子生成で埋める。
    キャラクリエイト個体も通常の新弟子と同様、必ず既存個体の中から師匠が自動選出される
    （本人が選ぶわけではない。新規開祖にはならない。一門は師匠の一門に入る）。
    pending_charactersの人数が固定枠数を上回っても（新人リーグの募集人数のズレなど）、
    全員そのまま参入させる（既存メンバーを強制引退させて枠を確保することはしない）。

    auto_fill_vacancy=Falseの場合、pending_charactersで埋まらない残り枠は
    自動生成の新弟子で埋めない（ブートストラップ第1季用。少人数スタートの意図を
    対局前に自動で埋めてしまわないため）。
    戻り値: (更新後のrosters dict, 新規参入者リスト, 更新後のname_registry, 引退者リスト
             （常に空リスト。新人受け入れ自体による強制引退は発生しないため、
              呼び出し側との互換のためだけに残している）)
    """
    if name_registry is None:
        name_registry = NameRegistry()

    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])

    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    d_departures = D_NEWCOMER_INTAKE
    created_characters = []
    if pending_characters:
        creation_pool = [ind for ind in (A + B + C + D) if not ind.retired]
        eligible_creation_masters = [ind for ind in creation_pool if ind.age >= MASTER_MIN_AGE] or creation_pool
        creation_pool_by_id = {ind.id: ind for ind in creation_pool}
        master_pick_counts = {}
        created_characters = []
        for i, req in enumerate(pending_characters):
            # 通常は新人リーグのロスターを組む時点で師弟関係が既に決まっている
            # （reqに"parent_a_id"が入っている）。入っていない場合（新人リーグを
            # 経由しないpending_characters.json経由の直接登録など）のみ、ここで初めて師匠を選ぶ
            if "parent_a_id" in req:
                master = None
            else:
                master = _pick_master(
                    eligible_creation_masters, titleholder_ids, allow_new_founder=False,
                    pick_counts=master_pick_counts,
                )
            ind = _build_character_creation_individual(req, season, i, master=master)
            # 新人リーグで師匠が決まってから実際に参入するまでの間に、師匠が一門ごと独立して
            # いることがあるため、一門は参入時点の師匠の一門に合わせる
            current_master = creation_pool_by_id.get(ind.parent_a_id)
            if current_master is not None:
                ind.clan_root_id = current_master.clan_root_id
            created_characters.append(ind)
        D.extend(created_characters)

    new_disciples = []
    remaining_slots = d_departures - len(created_characters)
    if auto_fill_vacancy and remaining_slots > 0:
        candidates = A + B + C + D
        new_disciples = generate_disciples(
            count=remaining_slots, season=season, pool=candidates, name_registry=name_registry,
            titleholder_ids=titleholder_ids,
        )
        D.extend(new_disciples)
    new_disciples = created_characters + new_disciples

    new_rosters = {"A": A, "B": B, "C": C, "D": D, FREE_CLASS: list(rosters.get(FREE_CLASS, []))}
    return new_rosters, new_disciples, name_registry, []


MASTER_MIN_AGE = 30  # 師匠になれる最低年齢（師匠より年下の弟子が生まれないようにするため）
AWAKENED_INITIAL_AGE_RANGE = (14, 16)  # 覚醒個体の参入年齢（通常は18〜24歳）

# 一門イベントの確率（新弟子1人あたり）。新人は必ず誰かの弟子として参入するため、
# 現在は師匠候補が誰もいない極端なケースでのみ師匠なしになる
CLAN_NEW_FOUNDER_CHANCE = 0.08
# 一門の継承と独立（継承型）。一門には「当主」がいて、最初の当主は開祖。
# ・代替わり：当主（宗家）が引退すると、嫡流で継ぐ。先代の弟子筋のうち先代に最も近い代の現役（ふつうは直弟子）から、
#   実績（タイトル期数→Aリーグ在籍季数→Elo）が最も上の者が次の当主になる。兄弟弟子の筋からは継がない
#   （家系の長子相続に近い形。弟子筋に現役がいなければ、そのときだけ流の中の別の筋から迎える＝養子）。当主は一門の流れ（開祖の名を取って
#   「◯◯流」）の名跡で、代数は筋が移っても通しで数える（囲碁の本因坊家が、強い門人を跡目に立てて世を重ねたのと同じ）。
#   一門は当主の名で「◯◯一門（開祖名流N代）」と呼ぶ。
#   このとき、新当主以外の先代の現役の直弟子のうち、タイトル5期以上で現役の直弟子が2人以上いる者は、
#   それぞれ子孫を引き連れて独立し、自分の一門を興す
#   （以前は先代の直弟子を最優先で当主にしていたため、タイトル0期の直弟子が、タイトル十数期の一門の実力者を差し置いて継いでいた）
# ・完全独立：当主が現役でも、タイトル CLAN_FULL_INDEPENDENCE_TITLES 期以上で、一門に残る現役の直弟子が
#   CLAN_FULL_INDEPENDENCE_DISCIPLES 人以上いる選手は、子孫を引き連れて独立し、自分の一門を興す。
#   当主本人は独立しない（当主＝その一門なので、継いだ後に一門を割って出ることはない）。
#   また、当主が自分の弟子筋にいる選手も独立しない（当主ごと連れ出すと一門の本流が抜けてしまうため）
# 以前は「本人＋同じ一門に残る子孫（引退者を含む通算）が6人以上」で機械的に独立させていたため、
# 実績の無い師匠も次々に独立し、一門が細かく割れ続けていた
CLAN_FULL_INDEPENDENCE_TITLES = 10
CLAN_FULL_INDEPENDENCE_DISCIPLES = 3
# 代替わりに伴う独立の条件：タイトル5期以上で、流に残る現役の直弟子が2人以上（一門を構えられる実績と弟子がいること）。
# 以前はタイトル1期でもあれば弟子がいなくても独立し、1〜4人で終わる小さな流が次々にできていた
CLAN_SUCCESSION_INDEPENDENCE_TITLES = 5
CLAN_SUCCESSION_INDEPENDENCE_DISCIPLES = 2
# 一家を成した選手の独立（成長型）：タイトル1期以上で、同じ流に残る自分の弟子筋が6人以上
CLAN_HOUSE_MIN_TITLES = 1
CLAN_HOUSE_MIN_LINE = 6


def _get(x, key):
    return x.get(key) if isinstance(x, dict) else getattr(x, key, None)


def _set(x, key, value):
    if isinstance(x, dict):
        x[key] = value
    else:
        setattr(x, key, value)


def apply_clan_succession(active_members, retired_records, season, heads, title_counts, a_counts):
    """季の開始時（新弟子の参入後）に一門の代替わり・独立を行う。
    active_members：現役の選手（Individualまたはdict）。retired_records：引退者アーカイブ（dict）。
    heads：{一門の開祖ID: {"head": 当主ID, "gen": 何代目か}}（その場で更新する。記録の無い一門は開祖が初代当主）。
    title_counts / a_counts：{ID: 前季までのタイトル獲得期数 / Aリーグ在籍季数}。
    独立した選手と、同じ一門に残っている子孫（現役・引退済みとも）のclan_root_idをその選手のIDに付け替える。
    戻り値：(独立イベントのリスト, 代替わりイベントのリスト)"""
    everyone = list(active_members) + list(retired_records)
    by_id = {_get(x, "id"): x for x in everyone}
    active_ids = {_get(x, "id") for x in active_members}
    children = {}
    for x in everyone:
        if _get(x, "parent_a_id"):
            children.setdefault(_get(x, "parent_a_id"), []).append(x)

    def clan_of(iid):
        return _get(by_id[iid], "clan_root_id")

    def head_of(clan):
        return heads.get(clan, {}).get("head", clan)

    def score(x, master_id=None):
        iid = _get(x, "id")
        return (title_counts.get(iid, 0), a_counts.get(iid, 0), _get(x, "parent_a_id") == master_id,
                _get(x, "elo") or 0, iid)

    def active_direct(master_id, clan):
        return [c for c in children.get(master_id, []) if _get(c, "id") in active_ids and _get(c, "clan_root_id") == clan]

    independence, successions = [], []

    def split(master, kind):
        mid, old = _get(master, "id"), _get(master, "clan_root_id")
        stack = [mid]
        while stack:
            pid = stack.pop()
            for c in children.get(pid, []):
                if _get(c, "clan_root_id") == old:
                    _set(c, "clan_root_id", mid)
                    stack.append(_get(c, "id"))
        _set(master, "clan_root_id", mid)
        _set(master, "independent_from_clan_id", old)
        _set(master, "independent_season", season)
        heads[mid] = {"head": mid, "gen": 1}
        independence.append({"id": mid, "name": _get(master, "display_name"), "from_clan_id": old,
                             "season": season, "kind": kind})

    # --- 本家の再興：現役が1人もいなくなった流でも、そこから独立した分家（孫分家以下も含む）に現役がいれば、
    #     分家の現役（各流の宗家・開祖を除く）のうち実績が最も上の選手を養子として迎え、宗家を継がせる。
    #     迎えるのは本人だけで、その弟子筋は分家に残る。強い弟子筋ほど分家として出ていくため、名門ほど本家が
    #     やせ細って先に途絶える（第139季の西村流）のを防ぐ。分家もすべて途絶えたときだけ本家も断絶する ---
    founders = {_get(x, "clan_root_id") for x in everyone}
    def branch_clans(clan):
        out, todo = set(), [clan]
        while todo:
            c0 = todo.pop()
            for f in founders:
                if f in by_id and f not in out and f != clan and _get(by_id[f], "clan_root_id") == f:
                    p = _get(by_id[f], "parent_a_id")
                    if p in by_id and _get(by_id[p], "clan_root_id") == c0:
                        out.add(f); todo.append(f)
        return out
    active_clans = {_get(x, "clan_root_id") for x in active_members}
    head_ids = {h.get("head") for h in heads.values()}
    for clan in sorted(founders - active_clans, key=lambda c: (_get(by_id[c], "generation") or 0) if c in by_id else 0):
        if clan not in by_id:
            continue
        branches = branch_clans(clan)
        pool = [x for x in active_members if _get(x, "clan_root_id") in branches
                and _get(x, "id") not in head_ids and _get(x, "clan_root_id") != _get(x, "id")]
        if not pool:
            continue
        heir = max(pool, key=score)
        from_clan = _get(heir, "clan_root_id")
        _set(heir, "clan_root_id", clan)
        prev = head_of(clan)
        gen = heads.get(clan, {}).get("gen", 1) + 1
        heads[clan] = {"head": _get(heir, "id"), "gen": gen}
        head_ids.add(_get(heir, "id"))
        successions.append({"clan_id": clan, "id": _get(heir, "id"), "name": _get(heir, "display_name"),
                            "prev_head_id": prev, "gen": gen, "season": season, "adopted": True,
                            "revival": True, "from_clan_id": from_clan})

    # --- 代替わり：開祖の世代が浅い一門から（親の一門で独立が起きた後の状態で子の一門を見る） ---
    clans = sorted({_get(x, "clan_root_id") for x in active_members},
                   key=lambda c: (_get(by_id[c], "generation") or 0) if c in by_id else 0)
    for clan in clans:
        head = head_of(clan)
        if head in active_ids or head not in by_id:
            continue
        members = [x for x in active_members if _get(x, "clan_root_id") == clan]
        if not members:
            continue
        direct = active_direct(head, clan)
        # 嫡流で継ぐ：先代の弟子筋（同じ流に残る子孫）のうち先代に最も近い代の現役から、実績が最も上の者。
        # 弟子筋に現役が1人もいなければ、そのときだけ流の中の別の筋から迎える（養子）
        line_gen = {}
        stack = [(head, 0)]
        while stack:
            pid, d = stack.pop()
            for c in children.get(pid, []):
                if _get(c, "clan_root_id") == clan and _get(c, "id") not in line_gen:
                    line_gen[_get(c, "id")] = d + 1
                    stack.append((_get(c, "id"), d + 1))
        line_active = [x for x in members if _get(x, "id") in line_gen]
        if line_active:
            near = min(line_gen[_get(x, "id")] for x in line_active)
            pool, adopted = [x for x in line_active if line_gen[_get(x, "id")] == near], False
        else:
            pool, adopted = members, True
        heir = max(pool, key=lambda x: score(x, head))
        gen = heads.get(clan, {}).get("gen", 1) + 1
        heads[clan] = {"head": _get(heir, "id"), "gen": gen}
        successions.append({"clan_id": clan, "id": _get(heir, "id"), "name": _get(heir, "display_name"),
                            "prev_head_id": head, "gen": gen, "season": season, "adopted": adopted})
        for c in direct:
            if (c is not heir and title_counts.get(_get(c, "id"), 0) >= CLAN_SUCCESSION_INDEPENDENCE_TITLES
                    and len(active_direct(_get(c, "id"), clan)) >= CLAN_SUCCESSION_INDEPENDENCE_DISCIPLES):
                split(c, "代替わり")

    # --- 完全独立：世代の浅い選手から ---
    for m in sorted(active_members, key=lambda x: _get(x, "generation") or 0):
        mid, clan = _get(m, "id"), _get(m, "clan_root_id")
        if clan == mid or head_of(clan) == mid:
            continue
        if title_counts.get(mid, 0) < CLAN_FULL_INDEPENDENCE_TITLES:
            continue
        if len(active_direct(mid, clan)) < CLAN_FULL_INDEPENDENCE_DISCIPLES:
            continue
        # 当主が自分の弟子筋にいる場合は独立しない（独立すると当主ごと一門を連れ出してしまい、一門の本流が抜けるため）
        stack, line = [mid], set()
        while stack:
            for c in children.get(stack.pop(), []):
                if _get(c, "clan_root_id") == clan and _get(c, "id") not in line:
                    line.add(_get(c, "id")); stack.append(_get(c, "id"))
        if head_of(clan) in line:
            continue
        split(m, "完全独立")

    # --- 一家を成した選手の独立（成長型）：宗家の代替わりを待たず、タイトル経験があり、同じ流に残る自分の
    #     弟子筋（孫弟子以下・引退者を含む）が CLAN_HOUSE_MIN_LINE 人以上になったら、自分の流を興す ---
    for m in sorted(active_members, key=lambda x: _get(x, "generation") or 0):
        mid, clan = _get(m, "id"), _get(m, "clan_root_id")
        if clan == mid or head_of(clan) == mid or title_counts.get(mid, 0) < CLAN_HOUSE_MIN_TITLES:
            continue
        stack, line = [mid], set()
        while stack:
            for c in children.get(stack.pop(), []):
                if _get(c, "clan_root_id") == clan and _get(c, "id") not in line:
                    line.add(_get(c, "id")); stack.append(_get(c, "id"))
        if len(line) < CLAN_HOUSE_MIN_LINE or head_of(clan) in line:
            continue
        split(m, "一家")
    return independence, successions


def _master_weight(ind, titleholder_ids, times_picked=0):
    """師匠として選ばれやすさの重み。Eloが高いほど、タイトルを保持しているほど選ばれやすくする
    （強い一門ほど自然と子孫を残しやすくなる。ベースは1.0なので誰にでもチャンスはある）。
    旧数値（÷200, +3.0）だと強い一門への集約が急激すぎたため、やや緩めた。
    times_pickedは、このバッチ（1回の新人リーグ・1回のD新弟子一括生成）の中で既に
    その個体が師匠として選ばれた回数。選ばれるたびに重みを半減させ、同じ1バッチの中で
    同じ師匠ばかりに弟子が集中するのを抑える（「強い一門ほど残りやすい」傾向自体は
    バッチをまたいで累積していくので保たれるが、1回の抽選で連続して同じ人に
    当たり続けるだけの不自然な偏りは減らす）"""
    elo_bonus = max(0.0, (ind.elo - 1500) / 300)
    title_bonus = 2.0 if ind.id in titleholder_ids else 0.0
    return (1.0 + elo_bonus + title_bonus) / (2 ** times_picked)


MAX_DISCIPLES_PER_BATCH = 2


def _pick_master(eligible_masters, titleholder_ids, allow_new_founder=True, pick_counts=None):
    """師匠を自動選出する（Elo・タイトル保持で重み付けした抽選）。
    allow_new_founder=Trueの場合、CLAN_NEW_FOUNDER_CHANCEの確率で師匠を持たない
    新規開祖になる（通常の新弟子生成と同じ）。Falseの場合は必ず誰かの弟子になる
    （候補が1人もいない極端なケースのみ例外的にNoneを返す）。
    pick_countsを渡すと、個体ID→このバッチ内で選ばれた回数の辞書として使い、
    選ばれるたびに加算する（呼び出し元はループの外で1つの辞書を使い回すことで、
    1バッチ内の重複選出を抑える）"""
    if not eligible_masters:
        return None
    if allow_new_founder and random.random() < CLAN_NEW_FOUNDER_CHANCE:
        return None
    counts = pick_counts if pick_counts is not None else {}
    # 1回のバッチ（新人リーグ1回・Dリーグ新弟子の一括生成1回）で1人の師匠に付く弟子は
    # MAX_DISCIPLES_PER_BATCH人まで（全員が上限に達している極端な場合のみ上限を無視する）
    if pick_counts is not None:
        eligible_masters = [ind for ind in eligible_masters
                            if counts.get(ind.id, 0) < MAX_DISCIPLES_PER_BATCH] or eligible_masters
    weights = [_master_weight(ind, titleholder_ids, counts.get(ind.id, 0)) for ind in eligible_masters]
    chosen = random.choices(eligible_masters, weights=weights, k=1)[0]
    if pick_counts is not None:
        pick_counts[chosen.id] = pick_counts.get(chosen.id, 0) + 1
    return chosen


# キャラクリエイト機能：サイトから指定されたタイプ傾向に応じて、該当パラメータの
# 抽選レンジを引き上げる（強制はせず、あくまで緩やかな傾向づけにとどめる）。
# タイプ傾向の代わりに、サイト側で直接8パラメータを割り振った場合（params）はそちらを優先する
CHARACTER_TYPE_BOOST_KEYS = {
    "balanced": [],
    "aggressive": ["mobility_weight", "frontier_weight"],
    "defensive": ["edge_stability_weight", "danger_zone_weight"],
    "corner": ["corner_weight", "center_weight"],
}

CHARACTER_PARAM_MIN = 0.1
CHARACTER_PARAM_MAX = 10.0
CHARACTER_PARAM_BUDGET = 40.0  # 8パラメータ合計の上限（サイト側の割り振りUIと一致させる）

# キャラクリエイト個体は、通常の新弟子（6%）よりも少しだけ覚醒しやすくする
CHARACTER_CREATION_AWAKENING_CHANCE = 0.08


def _character_creation_params(type_tendency):
    boosted = CHARACTER_TYPE_BOOST_KEYS.get(type_tendency, [])
    return {
        k: round(random.uniform(2.5, 6.0) if k in boosted else random.uniform(0.5, 5.0), 3)
        for k in PARAM_KEYS
    }


def _character_creation_params_from_custom(custom_params):
    """サイト側で直接割り振られた8パラメータを検証・正規化する。
    値の範囲・合計予算を超えていた場合は、比率を保ったまま予算内に収める"""
    values = {}
    for k in PARAM_KEYS:
        v = custom_params.get(k)
        if not isinstance(v, (int, float)):
            return None
        values[k] = max(CHARACTER_PARAM_MIN, min(CHARACTER_PARAM_MAX, float(v)))
    total = sum(values.values())
    if total > CHARACTER_PARAM_BUDGET:
        scale = CHARACTER_PARAM_BUDGET / total
        values = {k: v * scale for k, v in values.items()}
    return {k: round(v, 3) for k, v in values.items()}


def _build_character_creation_individual(request, season, index, master=None):
    """キャラクリエイトのリクエスト（{"name":, "type":, "params":, "awakened_param":}）から
    1体生成する。paramsが指定されていればそれを優先し、無ければtypeに応じたランダム
    生成にフォールバックする。

    この関数は新人リーグのロスターを組む時点（新人リーグの対局に使う個体を作る時）と、
    本戦での実際のDリーグ参入時（新人リーグの勝者を正式な個体にする時）の2回呼ばれ得る。
    師弟関係（parent_a_id・clan_root_id・generation）とparams・awakened_param・initial_age・
    volatilityは初回（新人リーグのロスターを組む時点）にのみ決定する。requestに既に
    "parent_a_id"キーがある場合はその時決定した値をそのまま使い、再抽選しない
    （新人リーグで戦った個体と実際にDリーグへ参入する個体が食い違わないようにする。
    なお新人リーグを経由しないpending_characters.json経由の直接登録など、師弟関係が
    まだ決まっていないリクエストに対しては、ここで初めてmasterから決定する）。

    masterが指定された場合、その個体の弟子として参入し、師匠の一門に入る。
    自動生成の新人（request["auto_generated"]）は通常の新弟子と同じく師匠の能力を
    継承（mutate_params）し、覚醒率も通常の新弟子と同じにする。
    キャラクリエイト投稿は師匠から継承せず、本人の指定/抽選値を使う
    （キャラクリエイトの趣旨は本人の狙った性能で参戦することのため）。"""
    ind_id = f"CC{season}-{index:03d}"

    if "parent_a_id" in request:
        # 新人リーグのロスターを組む時点で既に師弟関係・パラメータ等が決まっている
        params = request["params"]
        master_id = request.get("parent_a_id")
        clan_root_id = request.get("clan_root_id") or ind_id
        generation = request.get("generation", 0)
        volatility = request.get("volatility")
        awakened_key = request.get("awakened_param")
    elif master is not None and request.get("auto_generated"):
        params, generation = mutate_params(master)
        params, awakened_key = maybe_awaken(params, individual_id=ind_id)
        master_id = master.id
        clan_root_id = master.clan_root_id
        volatility = round(max(0.1, min(3.0, master.volatility + random.uniform(-0.2, 0.2))), 2)
    else:
        custom_params = request.get("params")
        params = (_character_creation_params_from_custom(custom_params) if isinstance(custom_params, dict) else None) \
            or _character_creation_params(request.get("type", "balanced"))
        params.update(birth_skills(master.params if master is not None else None))
        params, awakened_key = maybe_awaken(params, individual_id=ind_id, chance=CHARACTER_CREATION_AWAKENING_CHANCE)

        master_id = master.id if master is not None else None
        clan_root_id = master.clan_root_id if master is not None else ind_id
        generation = master.generation + 1 if master is not None else 0
        volatility = round(max(0.1, min(3.0, random.uniform(0.3, 2.0))), 2)

    # initial_ageが明示されていれば最優先で使う（新人リーグで持ち越し中の候補が、
    # 前回までの挑戦で重ねた年齢のまま再挑戦・参入する場合）。無指定なら従来通り、
    # 覚醒していれば若い年齢層、していなければLeagueIndividualの既定（18〜24歳）で抽選する
    if "initial_age" in request and request["initial_age"] is not None:
        resolved_initial_age = request["initial_age"]
    elif awakened_key:
        resolved_initial_age = random.randint(*AWAKENED_INITIAL_AGE_RANGE)
    else:
        resolved_initial_age = None

    ind = LeagueIndividual(
        ind_id, "D", params=params, generation=generation,
        parent_a_id=master_id, parent_b_id=None,
        display_name=request.get("name") or ind_id, clan_root_id=clan_root_id,
        # 性別が渡されていない場合（新人リーグの持ち越し候補で性別が抜け落ちていた不具合の名残等）は、
        # 名前から推定する（性別不明だとファンサイトの顔アイコンが表示されない）
        initial_age=resolved_initial_age, gender=request.get("gender") or infer_gender(request.get("name")),
    )
    ind.awakened_param = awakened_key
    ind.volatility = volatility if volatility is not None else round(max(0.1, min(3.0, random.uniform(0.3, 2.0))), 2)
    return ind


def generate_disciples(count, season, pool, name_registry, titleholder_ids=frozenset()):
    """
    新弟子（Dリーグ参入個体）を生成する。師弟関係のため、親（師匠）は常に1人。
    師匠はElo・タイトル保持で重み付けした抽選で選ばれる（強い一門ほど子孫を残しやすい）。
    師匠本人と子孫（同じ一門内、孫弟子以下も含む）が合わせてCLAN_BRANCH_MIN_MEMBERS人以上になると「分岐」（弟子ではあるが
    新しい一門の開祖になる）が起き、稀に「新規開祖」（師匠を持たず完全ランダムな
    能力で参入する）も起きる。
    """
    disciples = []

    active_pool_all = [ind for ind in pool if not ind.retired]
    # 師匠になれるのは一定年齢以上の個体のみ（該当者が誰もいない序盤などは制限なしにフォールバック）
    eligible_masters = [ind for ind in active_pool_all if ind.age >= MASTER_MIN_AGE] or active_pool_all
    master_pick_counts = {}

    for i in range(count):
        ind_id = f"D{season}-{i:03d}"
        display_name, gender = name_registry.generate()

        # 新人は必ず誰かの弟子として参入する（師匠候補が誰もいない場合のみ師匠なし）
        master = _pick_master(
            eligible_masters, titleholder_ids, allow_new_founder=False, pick_counts=master_pick_counts,
        )

        if master:
            params, gen = mutate_params(master)
            master_id = master.id
            clan_root_id = master.clan_root_id
        else:
            # 新規開祖は、既存の一門（世代を重ねて強化されてきた血統）に対抗できるよう、
            # 旧レンジ（0.5〜5.0、平均2.75）よりやや強めのベースラインで生成する
            params = {k: round(random.uniform(1.0, 7.0), 3) for k in PARAM_KEYS}
            params.update(birth_skills())
            gen = 0
            master_id = None
            clan_root_id = ind_id

        # 覚醒判定：覚醒した個体は「神童」的な扱いとして、通常より若い年齢で参入することがある
        params, awakened = maybe_awaken(params, individual_id=ind_id)
        initial_age = random.randint(*AWAKENED_INITIAL_AGE_RANGE) if awakened else None

        ind = LeagueIndividual(
            ind_id, "D", params=params, generation=gen,
            parent_a_id=master_id, parent_b_id=None, display_name=display_name,
            clan_root_id=clan_root_id, initial_age=initial_age, gender=gender,
        )
        ind.awakened_param = awakened

        # 師匠のムラ気を継承しつつ、少し変異（ノイズ）を加える
        base_vol = master.volatility if master else random.uniform(0.3, 2.0)

        # ムラ気の変異（±0.2程度）
        vol = base_vol + random.uniform(-0.2, 0.2)
        ind.volatility = max(0.1, min(3.0, round(vol, 2)))

        disciples.append(ind)

    return disciples


# 弟子が受け継ぐ能力の割合。残りはランダムな（リーグ平均的な合計の）能力で埋める。
# 師匠の能力をほぼそのまま（±15%）受け継ぐ方式だと、最強の師匠の弟子がほぼ最強の能力で
# 新人リーグに出てきて上位を独占してしまうため、強い一門の弟子は平均より強い程度に抑える。
# 割合は能力ごとに独立して抽選する（平均6割）。当初は全能力一律6割だったが、それだと弟子の強さが
# 師匠を中心にほぼ揃ってしまい、師匠と違う配分の組み合わせ（＝師匠を超える型）が生まれにくかった。
# シミュレーション（師匠2人×弟子40人×24局）では、一律6割に比べて弟子の平均はやや下がる
# （-1〜2ポイント）が、弟子どうしの差が広がって上振れした弟子が強くなった（上位4人の平均
# +1〜4ポイント）。新人リーグで上位だけが昇級するので、昇級者はやや強くなる方向に働く
DISCIPLE_INHERIT_RATIO_MIN = 0.2
DISCIPLE_INHERIT_RATIO_MAX = 1.0
DISCIPLE_BASELINE_SUM = 40.0  # ランダム部分の能力合計（現役選手の能力合計の平均程度）


def _baseline_params():
    raw = {k: random.uniform(0.5, 10.0) for k in PARAM_KEYS}
    scale = DISCIPLE_BASELINE_SUM / sum(raw.values())
    return {k: v * scale for k, v in raw.items()}


def mutate_params(master):
    """師匠の能力を能力ごとに抽選した割合（DISCIPLE_INHERIT_RATIO_MIN〜MAX）で受け継ぎ、
    残りをランダムな能力で埋め、さらに±5%の揺らぎを加える"""
    baseline = _baseline_params()
    child_params = {}
    for k in PARAM_KEYS:
        ratio = random.uniform(DISCIPLE_INHERIT_RATIO_MIN, DISCIPLE_INHERIT_RATIO_MAX)
        mixed = ratio * master.params.get(k, 1.0) + (1 - ratio) * baseline[k]
        child_params[k] = max(0.1, round(mixed * random.uniform(0.95, 1.05), 3))
    # 終盤力・定跡（skills.py）：終盤力は師匠から2割受け継ぎ、定跡は0から覚える
    child_params.update(birth_skills(master.params))

    return child_params, master.generation + 1
