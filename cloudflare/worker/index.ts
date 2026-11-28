// ============================================================
// サーキットオセロリーグ（othello.spia-net.com）の Worker
//
// ページ・画像・結果のJSONは静的ファイル（cloudflare/build/build.sh が公開する季の時点で作る）。
// この Worker は「.php」へのアクセスだけを受け持つ（wrangler.jsonc の assets.run_worker_first）。
//   api.php           結果の問い合わせ → 書き出し済みのJSON（_api/...）を返す。検索・名前での絞り込み・棋譜だけはここで組み立てる
//   predict.php       勝敗予想（D1）
//   board.php         掲示板（D1）
//   cheer.php         応援（D1）
//   article_like.php  記事のいいね（D1）
//   inquiry.php       問い合わせ（D1）
//   キャラクリエイトは休止中。寄付は OFUSE（ファンサイトのフッターのリンク）で受け、ここでは扱わない
// 中身は Xserver で動いていた同名のPHP（site/*.php）と同じ動きにしてある。
// ============================================================

export interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
}

const TITLES = ["青龍", "麒麟", "朱雀", "白虎", "玄武"];
const NG_WORDS = ["死ね", "殺す", "しね", "ころす", "殺害", "セックス", "ちんこ", "まんこ", "カジノ", "出会い系", "副業", "稼げる"];
// 運営の合言葉のSHA-256（site/admin_auth.php と同じ。合言葉そのものは置かない）
const ADMIN_KEY_SHA256 = "f8a8d9661f2aaddb52dbf08a7181c87517f517ea828f3b1c70c534b29af85b2e";

// ---------------- 共通 ----------------

function json(data: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

async function sha256(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function ipOf(req: Request): string {
  return req.headers.get("CF-Connecting-IP") || "unknown";
}

async function isAdminKey(key: unknown): Promise<boolean> {
  return typeof key === "string" && key !== "" && (await sha256(key)) === ADMIN_KEY_SHA256;
}

// 文字数（PHPの mb_strlen と同じく、コードポイント単位）
function len(s: string): number {
  return [...s].length;
}

async function readBody(req: Request): Promise<Record<string, any>> {
  try {
    const b = await req.json();
    return b && typeof b === "object" ? (b as Record<string, any>) : {};
  } catch {
    return {};
  }
}

// 日本時間の「今」（MySQL の NOW() と同じ書式。D1 にはこの書式で日本時間を入れる）
const NOW_JST = "datetime('now', '+9 hours')";
const TODAY_JST = "date('now', '+9 hours')";

// 書き出し済みの静的ファイル（_api/...）を読む
async function asset(env: Env, req: Request, path: string): Promise<Response | null> {
  const res = await env.ASSETS.fetch(new Request(new URL("/" + path, req.url).toString()));
  return res.ok ? res : null;
}
async function assetJson<T = any>(env: Env, req: Request, path: string): Promise<T | null> {
  const res = await asset(env, req, path);
  return res ? ((await res.json()) as T) : null;
}

// D1 の表（初回だけ作る）
let schemaReady: Promise<unknown> | null = null;
function ensureSchema(env: Env) {
  schemaReady ??= env.DB.batch([
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS predictions (
      user_id TEXT NOT NULL, season INTEGER NOT NULL, title TEXT NOT NULL, pick TEXT NOT NULL, nickname TEXT NOT NULL,
      ip_hash TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, season, title))`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS predictions_season ON predictions (season)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS predictions_ip ON predictions (ip_hash, updated_at)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS board_threads (
      id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL,
      is_admin INTEGER NOT NULL DEFAULT 0, ip_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_post_at TEXT NOT NULL,
      post_count INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS board_threads_list ON board_threads (deleted, last_post_at)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS board_posts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
      is_admin INTEGER NOT NULL DEFAULT 0, ip_hash TEXT NOT NULL, created_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS board_posts_thread ON board_posts (thread_id, id)`),
    env.DB.prepare(`CREATE INDEX IF NOT EXISTS board_posts_ip ON board_posts (ip_hash, created_at)`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS cheer_log (
      individual_id TEXT NOT NULL, ip_hash TEXT NOT NULL, cheered_date TEXT NOT NULL, message TEXT, created_at TEXT NOT NULL,
      PRIMARY KEY (individual_id, ip_hash, cheered_date))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS article_likes (
      article_id TEXT NOT NULL, ip_hash TEXT NOT NULL, liked_date TEXT NOT NULL, PRIMARY KEY (article_id, ip_hash, liked_date))`),
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS inquiries (
      id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, message TEXT NOT NULL, contact TEXT,
      ip_hash TEXT NOT NULL, created_at TEXT NOT NULL)`),
  ]).catch((e) => {
    schemaReady = null;
    throw e;
  });
  return schemaReady;
}

// ---------------- api.php（結果） ----------------

// cloudflare/build/export.py の api_key() と同じ規則で、静的ファイルの名前を作る
function encValue(v: string): string {
  if (/^[A-Za-z0-9_-]+$/.test(v)) return v;
  return "x" + [...new TextEncoder().encode(v)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function apiKey(action: string, params: Record<string, string>): string {
  const keys = Object.keys(params).sort();
  if (!keys.length) return `_api/${action}.json`;
  return `_api/${action}/${keys.map((k) => `${k}~${encValue(params[k])}`).join(".")}.json`;
}

// action ごとに、ファイル名に使うパラメータ（api.php が読むもの）
const PARAMS: Record<string, { name: string; int?: boolean }[]> = {
  season_awards: [{ name: "season", int: true }],
  standings: [{ name: "season", int: true }, { name: "individual_id" }],
  title_history_detail: [{ name: "title" }],
  title_bracket: [{ name: "season", int: true }, { name: "title" }],
  newcomer_league: [{ name: "for_season", int: true }],
  individual: [{ name: "id" }],
  clan: [{ name: "id" }],
};

async function handleApi(req: Request, env: Env): Promise<Response> {
  const q = new URL(req.url).searchParams;
  const action = q.get("action") || "leagues";
  const cache = { "Cache-Control": "public, max-age=60" };

  if (action === "search") {
    const word = (q.get("q") || "").trim();
    if (word === "" || len(word) > 20) return json({ results: [] }, 200, cache);
    const names = (await assetJson<[string, string, string | null, number][]>(env, req, "_api/x/names.json")) || [];
    const w = word.toLowerCase();
    const hit = (n: string) => (n || "").toLowerCase().includes(w);
    const active = names.filter((n) => n[3] === 0 && hit(n[1]));
    const retired = names.filter((n) => n[3] === 1 && hit(n[1]));
    const results = [...active, ...retired].slice(0, 30).map((n) => ({ id: n[0], display_name: n[1], league: n[2], retired: n[3] }));
    return json({ results }, 200, cache);
  }

  if (action === "recent_matches") {
    const limit = Math.min(500, Math.max(1, parseInt(q.get("limit") || "30", 10) || 30));
    const word = (q.get("q") || "").trim();
    if (word === "") {
      const data = await assetJson<{ matches: unknown[] }>(env, req, apiKey("recent_matches", { limit: "50" }));
      return json({ matches: (data?.matches || []).slice(0, limit) }, 200, cache);
    }
    // 名前に q を含む選手（最大20人）の直近の対局を合わせて、新しい順に並べる
    const names = (await assetJson<[string, string, string | null, number][]>(env, req, "_api/x/names.json")) || [];
    const w = word.toLowerCase();
    const ids = names.filter((n) => (n[1] || "").toLowerCase().includes(w)).slice(0, 20).map((n) => n[0]);
    const lists = await Promise.all(ids.map((id) => assetJson<any[]>(env, req, `_api/x/player_matches/${encodeURIComponent(id)}.json`)));
    const seen = new Set<number>();
    const rows: any[] = [];
    for (const list of lists) for (const r of list || []) if (!seen.has(r.id)) { seen.add(r.id); rows.push(r); }
    rows.sort((a, b) => b.season - a.season || b._o - a._o || b.id - a.id);
    return json({ matches: rows.slice(0, limit).map(({ _o, ...r }) => r) }, 200, cache);
  }

  if (action === "game") {
    const id = parseInt(q.get("id") || "", 10);
    if (!id) return json({ error: "id パラメータが必要です" }, 400);
    const index = (await assetJson<[number, number, number][]>(env, req, "_api/x/game_index.json")) || [];
    const range = index.find(([, lo, hi]) => id >= lo && id <= hi);
    const file = range ? await assetJson<{ matches: Record<string, any[]> }>(env, req, `_api/x/games/season_${range[0]}.json`) : null;
    const m = file?.matches[String(id)];
    if (!range || !m) return json({ error: "対局が見つかりません" }, 404);
    const [league, result, played_at, a, b, games] = m;
    for (const g of games) {
      if (g._packed) {
        g.moves = g.moves.map((v: number) => ({ pos: v & 63, color: v & 64 ? 2 : 1 }));
        delete g._packed;
      }
    }
    return json({ match: { id, season: range[0], league, result, played_at, individual_a_name: a, individual_b_name: b, games } }, 200, cache);
  }

  if (action === "retired_archive") {
    const limit = Math.min(200, Math.max(1, parseInt(q.get("limit") || "30", 10) || 30));
    const res = await asset(env, req, apiKey("retired_archive", { limit: limit <= 50 ? "50" : "100" }));
    if (!res) return json({ error: "見つかりません" }, 404);
    if (limit === 50 || limit === 100) return new Response(res.body, { headers: { "Content-Type": "application/json; charset=utf-8", ...cache } });
    const data: any = await res.json();
    if (Array.isArray(data.retired)) data.retired = data.retired.slice(0, limit);
    return json(data, 200, cache);
  }

  // それ以外：書き出し済みのファイルをそのまま返す
  const params: Record<string, string> = {};
  for (const p of PARAMS[action] || []) {
    const v = q.get(p.name);
    if (v === null) continue;
    params[p.name] = p.int ? String(parseInt(v, 10) || 0) : v;
  }
  const key = apiKey(action, params);
  const res = await asset(env, req, key);
  if (!res) return json({ error: `見つかりません（${action}）` }, 404);
  const statuses = (await assetJson<Record<string, number>>(env, req, "_api/x/status.json")) || {};
  const status = statuses[key] || 200;

  if (action === "individual" && status === 200) {
    // 応援の数は D1 から数えて差し込む（書き出したファイルの値は0のまま）
    const data: any = await res.json();
    if (data.individual) {
      try {
        await ensureSchema(env);
        const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM cheer_log WHERE individual_id = ?").bind(data.individual.id).first<{ n: number }>();
        data.individual.cheer_count = row?.n ?? 0;
      } catch {
        /* D1 が使えなくても結果は返す */
      }
    }
    return json(data, 200, { "Cache-Control": "no-store" });
  }
  return new Response(res.body, { status, headers: { "Content-Type": "application/json; charset=utf-8", ...cache } });
}

// ---------------- predict.php（勝敗予想） ----------------

type Holders = Record<string, Record<string, { id: string; name: string }>>;

function outcome(holders: Holders, s: number, t: string): "defend" | "change" | null {
  const cur = holders[s]?.[t];
  let prev = null;
  for (let k = s - 1; k >= 1; k--) if (holders[k]?.[t]) { prev = holders[k][t]; break; }
  if (!cur || !prev) return null;
  return cur.id === prev.id ? "defend" : "change";
}

async function handlePredict(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const state = await assetJson<any>(env, req, "_api/season_state.json");
  const released = Number(state?.season_state?.current_season || 0);
  const open = released + 1;
  const holders = (await assetJson<Holders>(env, req, "_api/x/title_holders.json")) || {};
  const validUser = (u: unknown): u is string => typeof u === "string" && /^[0-9a-f]{32}$/.test(u);

  if (req.method === "GET") {
    const q = new URL(req.url).searchParams;
    const action = q.get("action") || "status";
    const user = q.get("user") || "";
    if (action === "status") {
      const now: Record<string, unknown> = {};
      for (const t of TITLES) {
        let h = null;
        for (let k = released; k >= 1; k--) if (holders[k]?.[t]) { h = holders[k][t]; break; }
        now[t] = h;
      }
      const mine: Record<string, string> = {};
      let nickname: string | null = null;
      if (validUser(user)) {
        const r = await env.DB.prepare("SELECT title, pick, nickname FROM predictions WHERE user_id = ? AND season = ?").bind(user, open).all<any>();
        for (const row of r.results) { mine[row.title] = row.pick; nickname = row.nickname; }
      }
      const cnt = await env.DB.prepare("SELECT COUNT(DISTINCT user_id) AS n FROM predictions WHERE season = ?").bind(open).first<{ n: number }>();
      const share: Record<string, Record<string, number>> = {};
      const sh = await env.DB.prepare("SELECT title, pick, COUNT(*) AS n FROM predictions WHERE season = ? GROUP BY title, pick").bind(open).all<any>();
      for (const r of sh.results) (share[r.title] ??= {})[r.pick] = r.n;
      return json({ open_season: open, released_season: released, titles: TITLES, holders: now, mine, nickname, participants: cnt?.n ?? 0, share });
    }
    if (action === "ranking") {
      const rows = await env.DB.prepare("SELECT user_id, season, title, pick, nickname FROM predictions WHERE season <= ? ORDER BY season").bind(released).all<any>();
      const users = new Map<string, { points: number; answered: number; seasons: Set<number>; nickname: string }>();
      for (const p of rows.results) {
        const o = outcome(holders, p.season, p.title);
        if (o === null) continue;
        let u = users.get(p.user_id);
        if (!u) { u = { points: 0, answered: 0, seasons: new Set(), nickname: p.nickname }; users.set(p.user_id, u); }
        u.answered++;
        if (o === p.pick) u.points++;
        u.seasons.add(p.season);
        u.nickname = p.nickname;
      }
      const sorted = [...users.entries()].sort((a, b) => b[1].points - a[1].points || a[1].answered - b[1].answered);
      let rank = 0, prevPts: number | null = null, i = 0;
      const top: unknown[] = [];
      let me: unknown = null;
      for (const [uid, u] of sorted) {
        i++;
        if (u.points !== prevPts) { rank = i; prevPts = u.points; }
        const row = { rank, nickname: u.nickname, points: u.points, answered: u.answered, seasons: u.seasons.size };
        if (i <= 30) top.push(row);
        if (uid === user) me = row;
      }
      const minePrev: Record<string, string> = {};
      if (validUser(user)) {
        const r = await env.DB.prepare("SELECT title, pick FROM predictions WHERE user_id = ? AND season = ?").bind(user, released).all<any>();
        for (const row of r.results) minePrev[row.title] = row.pick;
      }
      const last = TITLES.map((t) => ({ title: t, outcome: outcome(holders, released, t), holder: holders[released]?.[t]?.name ?? null, mine: minePrev[t] ?? null }));
      return json({ released_season: released, participants: users.size, top, me, last });
    }
    return json({ error: "unknown action" }, 400);
  }

  if (req.method === "POST") {
    const body = await readBody(req);
    if (body.action !== "save") return json({ error: "unknown action" }, 400);
    const user = body.user;
    if (!validUser(user)) return json({ error: "利用者IDが不正です" }, 400);
    let nick = String(body.nickname ?? "").trim();
    const nickGiven = nick !== "";
    if (!nickGiven) {
      const prev = await env.DB.prepare("SELECT nickname FROM predictions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1").bind(user).first<{ nickname: string }>();
      nick = prev?.nickname || "名無しの予想家";
    }
    if (len(nick) > 20) return json({ error: "ニックネームは20文字までです" }, 400);
    if (/https?:|www\./i.test(nick)) return json({ error: "ニックネームにリンクは使えません" }, 400);
    if (NG_WORDS.some((w) => nick.includes(w))) return json({ error: "そのニックネームは使えません" }, 400);
    const picks = body.picks;
    if (!picks || typeof picks !== "object" || !Object.keys(picks).length) return json({ error: "予想がありません" }, 400);
    const ipHash = await sha256(ipOf(req) + "|othello-league-predict");
    const c = await env.DB.prepare(`SELECT COUNT(*) AS n FROM predictions WHERE ip_hash = ? AND updated_at >= datetime('now', '+9 hours', '-1 day')`).bind(ipHash).first<{ n: number }>();
    if ((c?.n ?? 0) >= 60 * 5) return json({ error: "今日はこれ以上保存できません。時間をおいてお試しください" }, 429);
    const stmts: D1PreparedStatement[] = [];
    for (const [t, p] of Object.entries(picks)) {
      if (!TITLES.includes(t) || (p !== "defend" && p !== "change")) continue;
      stmts.push(env.DB.prepare(`INSERT INTO predictions (user_id, season, title, pick, nickname, ip_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ${NOW_JST})
        ON CONFLICT (user_id, season, title) DO UPDATE SET pick = excluded.pick, nickname = excluded.nickname, ip_hash = excluded.ip_hash, updated_at = excluded.updated_at`)
        .bind(user, open, t, p, nick, ipHash));
    }
    // ニックネームは過去の予想にも揃える（ランキングの表示名）
    if (nickGiven) stmts.push(env.DB.prepare("UPDATE predictions SET nickname = ? WHERE user_id = ?").bind(nick, user));
    if (stmts.length) await env.DB.batch(stmts);
    return json({ ok: true, season: open, saved: stmts.length - (nickGiven ? 1 : 0) });
  }
  return json({ error: "method not allowed" }, 405);
}

// ---------------- board.php（掲示板） ----------------

const BOARD_CATEGORIES = ["要望", "バグ報告", "感想・雑談"];

async function handleBoard(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  if (req.method === "GET") {
    const q = new URL(req.url).searchParams;
    const action = q.get("action") || "list";
    if (action === "list") {
      const cat = q.get("category") || "";
      const useCat = BOARD_CATEGORIES.includes(cat);
      const sql = `SELECT id, category, title, author, is_admin, created_at, last_post_at, post_count FROM board_threads WHERE deleted = 0${useCat ? " AND category = ?" : ""} ORDER BY last_post_at DESC, id DESC LIMIT 100`;
      const st = useCat ? env.DB.prepare(sql).bind(cat) : env.DB.prepare(sql);
      const r = await st.all();
      return json({ categories: BOARD_CATEGORIES, threads: r.results });
    }
    if (action === "thread") {
      const id = parseInt(q.get("id") || "0", 10) || 0;
      const thread = await env.DB.prepare("SELECT id, category, title, author, is_admin, created_at, last_post_at, post_count FROM board_threads WHERE id = ? AND deleted = 0").bind(id).first();
      if (!thread) return json({ error: "スレッドが見つかりません" }, 404);
      const ps = await env.DB.prepare("SELECT id, author, body, is_admin, created_at, deleted FROM board_posts WHERE thread_id = ? ORDER BY id ASC").bind(id).all<any>();
      const posts = ps.results.map((p) => (p.deleted ? { ...p, body: "（この書き込みは削除されました）", author: "" } : p));
      return json({ thread, posts });
    }
    return json({ error: "不明な操作です" }, 400);
  }
  if (req.method !== "POST") return json({ error: "GETかPOSTのみ対応しています" }, 405);

  const body = await readBody(req);
  const action = String(body.action ?? "");
  const isAdmin = await isAdminKey(body.admin_key);
  if (action === "check_admin") return json({ admin: isAdmin });
  if (action === "delete") {
    if (!isAdmin) return json({ error: "合言葉が違います" }, 403);
    if (body.thread_id) await env.DB.prepare("UPDATE board_threads SET deleted = 1 WHERE id = ?").bind(parseInt(body.thread_id, 10)).run();
    else if (body.post_id) await env.DB.prepare("UPDATE board_posts SET deleted = 1 WHERE id = ?").bind(parseInt(body.post_id, 10)).run();
    return json({ success: true });
  }
  if (action !== "create" && action !== "reply") return json({ error: "不明な操作です" }, 400);

  let name = String(body.name ?? "").trim();
  const text = String(body.body ?? "").trim();
  if (isAdmin) name = "運営";
  if (name === "") name = "名無しさん";
  if (len(name) > 20) return json({ error: "名前は20文字以内にしてください" }, 400);
  if (text === "") return json({ error: "本文を入力してください" }, 400);
  if (len(text) > 1000) return json({ error: "本文は1000文字以内にしてください" }, 400);
  if (!isAdmin && name === "運営") return json({ error: "その名前は使えません" }, 400);
  const check = `${name}\n${text}\n${String(body.title ?? "")}`;
  if (!isAdmin) {
    const lower = check.toLowerCase();
    if (lower.includes("http") || lower.includes("www.")) return json({ error: "リンク（URL）を含む書き込みはできません" }, 400);
    if (NG_WORDS.some((w) => lower.includes(w.toLowerCase()))) return json({ error: "使えない言葉が含まれています" }, 400);
  }
  const ipHash = await sha256(ipOf(req) + "|othello-league-board");
  if (!isAdmin) {
    const r = await env.DB.prepare(`SELECT
        COALESCE(SUM(created_at >= datetime('now', '+9 hours', '-10 minutes')), 0) AS m10,
        COUNT(*) AS d1
      FROM board_posts WHERE ip_hash = ? AND created_at >= datetime('now', '+9 hours', '-1 day')`).bind(ipHash).first<{ m10: number; d1: number }>();
    if ((r?.m10 ?? 0) >= 3) return json({ error: "書き込みが続いています。少し時間をおいてください" }, 429);
    if ((r?.d1 ?? 0) >= 20) return json({ error: "本日の書き込みの上限に達しました" }, 429);
  }
  let threadId: number;
  if (action === "create") {
    const cat = String(body.category ?? "");
    const title = String(body.title ?? "").trim();
    if (!BOARD_CATEGORIES.includes(cat)) return json({ error: "カテゴリを選んでください" }, 400);
    if (title === "") return json({ error: "スレッドのタイトルを入力してください" }, 400);
    if (len(title) > 60) return json({ error: "タイトルは60文字以内にしてください" }, 400);
    const r = await env.DB.prepare(`INSERT INTO board_threads (category, title, author, is_admin, ip_hash, post_count, created_at, last_post_at)
      VALUES (?, ?, ?, ?, ?, 1, ${NOW_JST}, ${NOW_JST})`).bind(cat, title, name, isAdmin ? 1 : 0, ipHash).run();
    threadId = Number(r.meta.last_row_id);
  } else {
    threadId = parseInt(body.thread_id, 10) || 0;
    const t = await env.DB.prepare("SELECT id FROM board_threads WHERE id = ? AND deleted = 0").bind(threadId).first();
    if (!t) return json({ error: "スレッドが見つかりません" }, 404);
    await env.DB.prepare(`UPDATE board_threads SET post_count = post_count + 1, last_post_at = ${NOW_JST} WHERE id = ?`).bind(threadId).run();
  }
  await env.DB.prepare(`INSERT INTO board_posts (thread_id, author, body, is_admin, ip_hash, created_at) VALUES (?, ?, ?, ?, ?, ${NOW_JST})`)
    .bind(threadId, name, text, isAdmin ? 1 : 0, ipHash).run();
  return json({ success: true, thread_id: threadId });
}

// ---------------- cheer.php（応援） ----------------

async function handleCheer(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const q = new URL(req.url).searchParams;
  if (req.method === "GET" && q.get("action") === "comments") {
    const id = (q.get("individual_id") || "").trim();
    if (id === "" || len(id) > 32) return json({ error: "individual_id が不正です" }, 400);
    const limit = Math.min(50, Math.max(1, parseInt(q.get("limit") || "20", 10) || 20));
    const r = await env.DB.prepare("SELECT message, created_at FROM cheer_log WHERE individual_id = ? AND message IS NOT NULL AND message != '' ORDER BY created_at DESC LIMIT ?").bind(id, limit).all();
    return json({ comments: r.results });
  }
  if (req.method !== "POST") return json({ error: "GET(action=comments)またはPOSTのみ対応しています" }, 405);
  const body = await readBody(req);
  const id = String(body.individual_id ?? "").trim();
  if (id === "" || len(id) > 32) return json({ error: "individual_id が不正です" }, 400);
  // 実在する選手か（公開済みの季までに登場した選手だけ）
  const names = (await assetJson<[string, string, string | null, number][]>(env, req, "_api/x/names.json")) || [];
  if (!names.some((n) => n[0] === id)) return json({ error: "選手が見つかりません" }, 404);
  let message = String(body.message ?? "").trim();
  if (len(message) > 140) message = [...message].slice(0, 140).join("");
  const ipHash = await sha256(ipOf(req) + "|othello-league-cheer");
  const r = await env.DB.prepare(`INSERT OR IGNORE INTO cheer_log (individual_id, ip_hash, cheered_date, message, created_at) VALUES (?, ?, ${TODAY_JST}, ?, ${NOW_JST})`)
    .bind(id, ipHash, message !== "" ? message : null).run();
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM cheer_log WHERE individual_id = ?").bind(id).first<{ n: number }>();
  return json({ success: true, already_cheered: r.meta.changes === 0, cheer_count: count?.n ?? 0 });
}

// ---------------- article_like.php（記事のいいね） ----------------

async function handleArticleLike(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const q = new URL(req.url).searchParams;
  const countOf = async (id: string) =>
    (await env.DB.prepare("SELECT COUNT(*) AS n FROM article_likes WHERE article_id = ?").bind(id).first<{ n: number }>())?.n ?? 0;
  if (req.method === "GET" && q.get("action") === "counts") {
    const r = await env.DB.prepare("SELECT article_id, COUNT(*) AS like_count FROM article_likes GROUP BY article_id").all<any>();
    const counts: Record<string, number> = {};
    for (const row of r.results) counts[row.article_id] = row.like_count;
    return json({ counts });
  }
  if (req.method === "GET") {
    const id = (q.get("article_id") || "").trim();
    if (id === "" || len(id) > 64) return json({ error: "article_id が不正です" }, 400);
    return json({ like_count: await countOf(id) });
  }
  if (req.method !== "POST") return json({ error: "GETまたはPOSTのみ対応しています" }, 405);
  const body = await readBody(req);
  const id = String(body.article_id ?? "").trim();
  if (id === "" || len(id) > 64) return json({ error: "article_id が不正です" }, 400);
  const ipHash = await sha256(ipOf(req) + "|othello-league-article-like");
  const r = await env.DB.prepare(`INSERT OR IGNORE INTO article_likes (article_id, ip_hash, liked_date) VALUES (?, ?, ${TODAY_JST})`).bind(id, ipHash).run();
  return json({ success: true, already_liked: r.meta.changes === 0, like_count: await countOf(id) });
}

// ---------------- inquiry.php（問い合わせ） ----------------

async function handleInquiry(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  if (req.method !== "POST") return json({ error: "POSTのみ対応しています" }, 405);
  const body = await readBody(req);
  if (body.action === "list") {
    if (!(await isAdminKey(body.admin_key))) return json({ error: "合言葉が違います" }, 403);
    const r = await env.DB.prepare("SELECT id, category, message, contact, created_at FROM inquiries ORDER BY id DESC LIMIT 200").all();
    return json({ inquiries: r.results });
  }
  let category = String(body.category ?? "その他").trim();
  const message = String(body.message ?? "").trim();
  const contact = String(body.contact ?? "").trim();
  if (!["新規棋戦の提案", "バグ報告", "ご意見・ご感想", "その他"].includes(category)) category = "その他";
  if (message === "") return json({ error: "内容を入力してください" }, 400);
  if (len(message) > 1000) return json({ error: "内容は1000文字以内で入力してください" }, 400);
  if (len(contact) > 100) return json({ error: "返信先は100文字以内で入力してください" }, 400);
  const ipHash = await sha256(ipOf(req) + "|othello-league-inquiry");
  const c = await env.DB.prepare(`SELECT COUNT(*) AS n FROM inquiries WHERE ip_hash = ? AND created_at >= datetime('now', '+9 hours', '-1 day')`).bind(ipHash).first<{ n: number }>();
  if ((c?.n ?? 0) >= 5) return json({ error: "本日の送信上限に達しました。また明日お試しください。" }, 429);
  await env.DB.prepare(`INSERT INTO inquiries (category, message, contact, ip_hash, created_at) VALUES (?, ?, ?, ?, ${NOW_JST})`)
    .bind(category, message, contact !== "" ? contact : null, ipHash).run();
  return json({ success: true, message: "お問い合わせを受け付けました。内容によっては返信できない場合があります。" });
}

// ---------------- 入口 ----------------

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const path = new URL(req.url).pathname;
    try {
      switch (path) {
        case "/api.php": return await handleApi(req, env);
        case "/predict.php": return await handlePredict(req, env);
        case "/board.php": return await handleBoard(req, env);
        case "/cheer.php": return await handleCheer(req, env);
        case "/article_like.php": return await handleArticleLike(req, env);
        case "/inquiry.php": return await handleInquiry(req, env);
        case "/create_character.php":
          return json({ error: "キャラクリエイトは休止中です。", closed: true }, 429);
        default:
          // ない .php
          return json({ error: "この機能は休止中です" }, 404);
      }
    } catch (e) {
      console.error(e);
      return json({ error: "サーバーで問題が起きました。時間をおいてお試しください" }, 500);
    }
  },
} satisfies ExportedHandler<Env>;
