#!/usr/bin/env python3
"""データサイトの api.php の結果を、全ページ分JSONファイルに書き出す。

一時的なMySQL（import.php で取り込み済み）につないだ api.php を PHP の組み込みサーバーで動かし、
サイトが使う action とパラメータの組み合わせをすべて呼んで、Cloudflare の静的ファイルとして置ける形で保存する。
Worker（cloudflare/worker/index.ts）は api.php?action=...&... への問い合わせを、同じ規則で作ったファイル名に読み替える。

ファイル名の規則（Worker の apiKey() と同じ）：
  パラメータ無し      _api/{action}.json
  パラメータあり      _api/{action}/{k1}~{v1}.{k2}~{v2}.json   （キーの名前順。値は英数字・-・_ だけならそのまま、
                      それ以外は "x" + UTF-8 の16進数）

使い方：python3 export.py <サイトの作業ディレクトリ（api.php・db_config.php を置いたところ）> <出力ディレクトリ>
"""
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

PORT = 8811
TITLES = ["青龍", "麒麟", "朱雀", "白虎", "玄武"]


def enc_value(v):
    v = str(v)
    return v if re.fullmatch(r"[A-Za-z0-9_-]+", v) else "x" + v.encode("utf-8").hex()


def api_key(action, params):
    if not params:
        return f"_api/{action}.json"
    parts = [f"{k}~{enc_value(params[k])}" for k in sorted(params)]
    return f"_api/{action}/{'.'.join(parts)}.json"


def fetch(action, params):
    q = urllib.parse.urlencode({"action": action, **params})
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api.php?{q}", timeout=300) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:  # api.php の json_error（404など）もそのまま残す
        return e.code, e.read()


def main():
    web, out = sys.argv[1], sys.argv[2]
    env = dict(os.environ, PHP_CLI_SERVER_WORKERS="8")
    server = subprocess.Popen(["php", "-d", "memory_limit=1024M", "-S", f"127.0.0.1:{PORT}", "-t", web],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        for _ in range(50):
            try:
                fetch("season_state", {})
                break
            except Exception:
                time.sleep(0.2)

        def get_json(action, params=None):
            status, body = fetch(action, params or {})
            return json.loads(body)

        current = int(get_json("season_state")["season_state"]["current_season"])
        names = json.load(open(os.path.join(out, "_api/x/names.json"), encoding="utf-8"))
        ids = [n[0] for n in names]
        nl_seasons = get_json("newcomer_league").get("available_seasons", [])

        jobs = [
            ("season_state", {}), ("leagues", {}), ("titles", {}), ("standings", {}), ("hall_of_fame", {}),
            ("lineage", {}), ("awakened_list", {}), ("newcomer_league", {}), ("newcomer_league_history", {}),
            ("season_awards", {}), ("retired_archive", {"limit": 50}), ("retired_archive", {"limit": 100}),
            ("recent_matches", {"limit": 50}), ("clans", {}),
        ]
        jobs += [("title_history_detail", {"title": t}) for t in TITLES]
        for s in range(1, current + 1):
            jobs.append(("standings", {"season": s}))
            jobs.append(("season_awards", {"season": s}))
            jobs += [("title_bracket", {"season": s, "title": t}) for t in TITLES]
        jobs += [("newcomer_league", {"for_season": s}) for s in nl_seasons]
        for i in ids:
            jobs.append(("individual", {"id": i}))
            jobs.append(("standings", {"individual_id": i}))

        def run(job):
            action, params = job
            status, body = fetch(action, params)
            data = json.loads(body)
            path = os.path.join(out, api_key(action, params))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            return status

        # 200以外（api.php が json_error で返したもの）は、Worker が同じ状態コードで返せるように一覧にしておく
        statuses = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for (action, params), status in zip(jobs, ex.map(run, jobs)):
                if status >= 500:
                    raise SystemExit(f"api.php がエラーを返しました：{action} {params} {status}")
                if status != 200:
                    statuses[api_key(action, params)] = status
        with open(os.path.join(out, "_api/x/status.json"), "w", encoding="utf-8") as f:
            json.dump(statuses, f, ensure_ascii=False)
        print(f"書き出し（api.php）：第{current}季時点 {len(jobs)}件（200以外 {len(statuses)}件）")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
