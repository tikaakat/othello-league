"""
対局の並列実行。

GitHub Actionsの実行環境はCPUが4つあるが、以前は1局ずつ順番に対局していたため、3つは遊んでいた。
同じ個体が出ない対局どうしは互いに影響しない（先後の割り振りに使う黒番回数も変わらない）ので、
まとめて別プロセスで同時に打たせ、結果は元の順番どおりに返す。

  - 先後の割り振り：各対局の両者について、それより前に並んでいる対局がすべて終わってから決める
    （1局ずつ順番に打っていた頃と同じ黒番回数で判定する）。同じ個体が出る対局は、前の対局が
    終わるまで始めない
  - 乱数：対局ごとの乱数の種を、元の並び順でメインプロセス側から配る。並列数（OTHELLO_WORKERS、
    既定はCPU数）を変えても同じ結果になる
  - Elo更新・勝ち点・ログは、呼び出し側で元の並び順に適用する（Eloは指し手に影響しない）
"""
import atexit
import os
import random
import multiprocessing

from .play import _play, _outcome_from_score, MAX_REMATCH_ATTEMPTS
from .buffs import effective_params

_POOL = None


def _workers():
    try:
        return max(1, int(os.environ.get("OTHELLO_WORKERS", "") or os.cpu_count() or 1))
    except ValueError:
        return 1


def _get_pool():
    global _POOL
    if _POOL is None:
        _POOL = multiprocessing.get_context("fork").Pool(_workers())
        atexit.register(_POOL.terminate)
    return _POOL


def _match_play(params_a, params_b, vol_a, vol_b, a_is_black, depth, attempts):
    """1対局（引き分け時の再戦を含む）を打つ。個体オブジェクトは渡さず、必要な値だけ受け取る"""
    games = []
    a_black = a_white = 0
    for _ in range(attempts):
        if a_is_black:
            black, white, moves = _play(params_a, params_b, depth, depth,
                                         noise_scale_black=vol_a, noise_scale_white=vol_b)
            a_black += 1
        else:
            black, white, moves = _play(params_b, params_a, depth, depth,
                                         noise_scale_black=vol_b, noise_scale_white=vol_a)
            a_white += 1
        my_score = black if a_is_black else white
        opp_score = white if a_is_black else black
        outcome_a = _outcome_from_score(my_score, opp_score)
        games.append({"outcome_a": outcome_a, "moves": moves, "black": black, "white": white,
                      "a_was_black": a_is_black})
        if outcome_a != "draw":
            return outcome_a, games, a_black, a_white
        a_is_black = not a_is_black
    return "draw", games, a_black, a_white


def _call_worker(args):
    fn, fargs, seed = args
    random.seed(seed)
    return fn(*fargs)


def run_jobs(jobs):
    """jobs（(fn, args) のリスト）を別プロセスで同時に実行し、結果を元の順で返す。
    乱数の種は元の並び順でメインプロセス側から配るため、並列数を変えても同じ結果になる。
    fn はモジュールの最上位で定義された関数であること（別プロセスへ渡すため）"""
    seeded = [(fn, tuple(args), random.randrange(2 ** 31)) for fn, args in jobs]
    if _workers() > 1 and len(seeded) > 1:
        return _get_pool().map(_call_worker, seeded, chunksize=1)
    state = random.getstate()
    outs = [_call_worker(j) for j in seeded]
    random.setstate(state)
    return outs


def run_calls(fn, arglist):
    return run_jobs([(fn, args) for args in arglist])


# ------------------------------------------------------------
# 同時進行の仕組み。リーグ戦・トーナメントなどの各イベントを「次に同時に指せる対局（job）の一覧」を
# yieldし、結果の一覧を受け取って先へ進むジェネレータとして書く。drive() は複数のイベントの
# 「次の一手」をまとめて1度に並列実行するので、リーグ戦とタイトル戦の予選のように互いに依存しない
# イベントを同時に進められ、どれかが終盤で対局数が減ってもCPUが遊ばない
# ------------------------------------------------------------
def drive(*gens):
    """イベントのジェネレータを同時に進め、それぞれの戻り値を並べて返す"""
    results = [None] * len(gens)
    pending = {}
    for i, g in enumerate(gens):
        try:
            pending[i] = next(g)
        except StopIteration as e:
            results[i] = e.value
    while pending:
        order = list(pending)
        outs = run_jobs([job for i in order for job in pending[i]])
        pos = 0
        for i in order:
            n = len(pending[i])
            try:
                pending[i] = gens[i].send(outs[pos:pos + n])
            except StopIteration as e:
                results[i] = e.value
                del pending[i]
            pos += n
    return results


def match_waves(pairs, depth=4, allow_rematch=True, decisive=False, max_decisive_attempts=100):
    """
    pairs（(ind_a, ind_b)のリスト）を、1局ずつ順番に play_league_match / play_decisive_match を
    呼んだのと同じ規則で対局し、[(outcome_a, games), ...] を元の並び順で返す（yield from で使う）。
    個体の black_count / white_count も同じように更新する。
    decisive=True なら引き分けを許さず決着まで打ち直す（play_decisive_match相当）。
      - 先後の割り振り：各対局の両者について、それより前に並んでいる対局がすべて終わってから決める
        （同じ個体が出る対局は、前の対局が終わるまで始めない）
    """
    attempts = max_decisive_attempts if decisive else (MAX_REMATCH_ATTEMPTS if allow_rematch else 1)
    results = [None] * len(pairs)
    remaining = list(range(len(pairs)))
    while remaining:
        # 前に並ぶ未対局の対局と個体が重ならないものを、この回にまとめて打つ
        busy, wave, rest = set(), [], []
        for i in remaining:
            a, b = pairs[i]
            if a.id in busy or b.id in busy:
                rest.append(i)
            else:
                wave.append(i)
            busy.add(a.id); busy.add(b.id)
        jobs = []
        for i in wave:
            a, b = pairs[i]
            if a.black_count < b.black_count:
                a_is_black = True
            elif a.black_count > b.black_count:
                a_is_black = False
            else:
                a_is_black = random.choice([True, False])
            jobs.append((_match_play, (effective_params(a), effective_params(b), a.volatility, b.volatility,
                                       a_is_black, depth, attempts)))
        outs = yield jobs
        for i, (outcome_a, games, a_black, a_white) in zip(wave, outs):
            a, b = pairs[i]
            a.black_count += a_black; b.white_count += a_black
            b.black_count += a_white; a.white_count += a_white
            results[i] = (outcome_a, games)
        remaining = rest
    return results


def run_matches(pairs, depth=4, allow_rematch=True, decisive=False, max_decisive_attempts=100):
    """match_waves を単独で実行する"""
    return drive(match_waves(pairs, depth, allow_rematch, decisive, max_decisive_attempts))[0]
