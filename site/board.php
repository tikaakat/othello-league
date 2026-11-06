<?php
// ============================================================
// 掲示板（ファンサイトの「掲示板」タブ）：カテゴリ別のスレッドに誰でも書き込める。
// 運営は合言葉（admin_auth.php）を入れると「運営」の印付きで書き込め、投稿・スレッドを削除できる。
//
// GET  ?action=list[&category=要望]   スレッド一覧（最終書き込みの新しい順）
// GET  ?action=thread&id=N             スレッドと書き込み
// POST {"action":"create","category","title","name","body"[,"admin_key"]}  スレッドを立てる
// POST {"action":"reply","thread_id","name","body"[,"admin_key"]}          返信する
// POST {"action":"delete","admin_key","post_id"|"thread_id"}               削除（運営のみ）
// POST {"action":"check_admin","admin_key"}                                合言葉の確認
//
// 荒らし対策：同じ接続元からは10分に3件・1日に20件まで。リンク（http）とNGワードを含む書き込みは受け付けない。
// 接続元のIPは生のまま保存せず、ハッシュにして連投の判定にだけ使う。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';
require_once __DIR__ . '/admin_auth.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

const BOARD_CATEGORIES = ['要望', 'バグ報告', '感想・雑談'];
const BOARD_NG_WORDS = ['死ね', '殺す', 'しね', 'ころす', '殺害', 'セックス', 'ちんこ', 'まんこ', 'カジノ', '出会い系', '副業', '稼げる'];
const BOARD_LIMIT_10MIN = 3;
const BOARD_LIMIT_DAY = 20;

function out($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$pdo = league_db_connect();
$pdo->exec("CREATE TABLE IF NOT EXISTS board_threads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    category VARCHAR(16) NOT NULL,
    title VARCHAR(60) NOT NULL,
    author VARCHAR(20) NOT NULL,
    is_admin TINYINT NOT NULL DEFAULT 0,
    ip_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_post_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    post_count INT NOT NULL DEFAULT 0,
    deleted TINYINT NOT NULL DEFAULT 0,
    INDEX (deleted, last_post_at)
) DEFAULT CHARSET=utf8mb4");
$pdo->exec("CREATE TABLE IF NOT EXISTS board_posts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    thread_id INT NOT NULL,
    author VARCHAR(20) NOT NULL,
    body VARCHAR(1000) NOT NULL,
    is_admin TINYINT NOT NULL DEFAULT 0,
    ip_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    INDEX (thread_id, id),
    INDEX (ip_hash, created_at)
) DEFAULT CHARSET=utf8mb4");

$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET') {
    $action = $_GET['action'] ?? 'list';
    if ($action === 'list') {
        $cat = (string)($_GET['category'] ?? '');
        $sql = "SELECT id, category, title, author, is_admin, created_at, last_post_at, post_count
                FROM board_threads WHERE deleted = 0";
        $params = [];
        if (in_array($cat, BOARD_CATEGORIES, true)) { $sql .= " AND category = :c"; $params['c'] = $cat; }
        $sql .= " ORDER BY last_post_at DESC LIMIT 100";
        $st = $pdo->prepare($sql);
        $st->execute($params);
        out(['categories' => BOARD_CATEGORIES, 'threads' => $st->fetchAll()]);
    }
    if ($action === 'thread') {
        $id = (int)($_GET['id'] ?? 0);
        $st = $pdo->prepare("SELECT id, category, title, author, is_admin, created_at, last_post_at, post_count
                             FROM board_threads WHERE id = :id AND deleted = 0");
        $st->execute(['id' => $id]);
        $thread = $st->fetch();
        if (!$thread) out(['error' => 'スレッドが見つかりません'], 404);
        $ps = $pdo->prepare("SELECT id, author, body, is_admin, created_at, deleted FROM board_posts
                             WHERE thread_id = :id ORDER BY id ASC");
        $ps->execute(['id' => $id]);
        $posts = array_map(function ($p) {
            if ((int)$p['deleted']) { $p['body'] = '（この書き込みは削除されました）'; $p['author'] = ''; }
            return $p;
        }, $ps->fetchAll());
        out(['thread' => $thread, 'posts' => $posts]);
    }
    out(['error' => '不明な操作です'], 400);
}

if ($method !== 'POST') out(['error' => 'GETかPOSTのみ対応しています'], 405);

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$action = (string)($body['action'] ?? '');
$isAdmin = is_admin_key($body['admin_key'] ?? '');

if ($action === 'check_admin') out(['admin' => $isAdmin]);

if ($action === 'delete') {
    if (!$isAdmin) out(['error' => '合言葉が違います'], 403);
    if (!empty($body['thread_id'])) {
        $pdo->prepare("UPDATE board_threads SET deleted = 1 WHERE id = :id")->execute(['id' => (int)$body['thread_id']]);
    } elseif (!empty($body['post_id'])) {
        $pdo->prepare("UPDATE board_posts SET deleted = 1 WHERE id = :id")->execute(['id' => (int)$body['post_id']]);
    }
    out(['success' => true]);
}

if ($action !== 'create' && $action !== 'reply') out(['error' => '不明な操作です'], 400);

// --- 入力の確認 ---
$name = trim((string)($body['name'] ?? ''));
$text = trim((string)($body['body'] ?? ''));
if ($isAdmin) $name = '運営';
if ($name === '') $name = '名無しさん';
if (mb_strlen($name) > 20) out(['error' => '名前は20文字以内にしてください'], 400);
if ($text === '') out(['error' => '本文を入力してください'], 400);
if (mb_strlen($text) > 1000) out(['error' => '本文は1000文字以内にしてください'], 400);
if (!$isAdmin && $name === '運営') out(['error' => 'その名前は使えません'], 400);

$check = $name . "\n" . $text . "\n" . (string)($body['title'] ?? '');
if (!$isAdmin) {
    if (stripos($check, 'http') !== false || stripos($check, 'www.') !== false) {
        out(['error' => 'リンク（URL）を含む書き込みはできません'], 400);
    }
    foreach (BOARD_NG_WORDS as $w) {
        if (mb_stripos($check, $w) !== false) out(['error' => '使えない言葉が含まれています'], 400);
    }
}

$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-board');
if (!$isAdmin) {
    $rs = $pdo->prepare("SELECT
        SUM(created_at >= (NOW() - INTERVAL 10 MINUTE)) AS m10,
        SUM(created_at >= (NOW() - INTERVAL 1 DAY)) AS d1
        FROM board_posts WHERE ip_hash = :ip AND created_at >= (NOW() - INTERVAL 1 DAY)");
    $rs->execute(['ip' => $ipHash]);
    $r = $rs->fetch();
    if ((int)$r['m10'] >= BOARD_LIMIT_10MIN) out(['error' => '書き込みが続いています。少し時間をおいてください'], 429);
    if ((int)$r['d1'] >= BOARD_LIMIT_DAY) out(['error' => '本日の書き込みの上限に達しました'], 429);
}

if ($action === 'create') {
    $cat = (string)($body['category'] ?? '');
    $title = trim((string)($body['title'] ?? ''));
    if (!in_array($cat, BOARD_CATEGORIES, true)) out(['error' => 'カテゴリを選んでください'], 400);
    if ($title === '') out(['error' => 'スレッドのタイトルを入力してください'], 400);
    if (mb_strlen($title) > 60) out(['error' => 'タイトルは60文字以内にしてください'], 400);
    $pdo->prepare("INSERT INTO board_threads (category, title, author, is_admin, ip_hash, post_count)
                   VALUES (:c, :t, :a, :adm, :ip, 1)")
        ->execute(['c' => $cat, 't' => $title, 'a' => $name, 'adm' => $isAdmin ? 1 : 0, 'ip' => $ipHash]);
    $threadId = (int)$pdo->lastInsertId();
} else {
    $threadId = (int)($body['thread_id'] ?? 0);
    $st = $pdo->prepare("SELECT id FROM board_threads WHERE id = :id AND deleted = 0");
    $st->execute(['id' => $threadId]);
    if (!$st->fetch()) out(['error' => 'スレッドが見つかりません'], 404);
    $pdo->prepare("UPDATE board_threads SET post_count = post_count + 1, last_post_at = NOW() WHERE id = :id")
        ->execute(['id' => $threadId]);
}
$pdo->prepare("INSERT INTO board_posts (thread_id, author, body, is_admin, ip_hash) VALUES (:t, :a, :b, :adm, :ip)")
    ->execute(['t' => $threadId, 'a' => $name, 'b' => $text, 'adm' => $isAdmin ? 1 : 0, 'ip' => $ipHash]);

out(['success' => true, 'thread_id' => $threadId]);
