<?php
// ============================================================
// news-site（オセロ界タイムズ）の記事への「いいね」機能。
// 記事そのものはdata/news/articles.json側で管理しており、このDBには
// 記事の行が無いため、cheer.php（個体への応援）と違ってカウンタ列は持たず、
// article_likesのログ行数（COUNT(*)）をそのままいいね数として扱う。
// GET（article_id指定）：1記事の件数取得のみ（書き込みなし）。
// GET（action=counts）：いいねが1件以上ついている全記事分の件数を一括取得する
// （news-siteの人気記事ランキング表示用。記事ごとに個別リクエストしなくて済むようにする）。
// POST：いいねを1件記録する。乱用防止のため、同一IP・同一記事では1日1回までに制限する。
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$pdo = league_db_connect();

function fetch_like_count($pdo, $articleId) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM article_likes WHERE article_id = :id");
    $stmt->execute(['id' => $articleId]);
    return (int)$stmt->fetchColumn();
}

if ($_SERVER['REQUEST_METHOD'] === 'GET' && ($_GET['action'] ?? '') === 'counts') {
    $stmt = $pdo->query("SELECT article_id, COUNT(*) AS like_count FROM article_likes GROUP BY article_id");
    $counts = [];
    foreach ($stmt->fetchAll() as $row) {
        $counts[$row['article_id']] = (int)$row['like_count'];
    }
    echo json_encode(['counts' => $counts], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $articleId = trim((string)($_GET['article_id'] ?? ''));
    if ($articleId === '' || mb_strlen($articleId) > 64) {
        http_response_code(400);
        echo json_encode(['error' => 'article_id が不正です']);
        exit;
    }
    echo json_encode(['like_count' => fetch_like_count($pdo, $articleId)], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'GETまたはPOSTのみ対応しています']);
    exit;
}

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$articleId = trim((string)($body['article_id'] ?? ''));
if ($articleId === '' || mb_strlen($articleId) > 64) {
    http_response_code(400);
    echo json_encode(['error' => 'article_id が不正です']);
    exit;
}

// IPアドレスは生のまま保存せず、ハッシュ化してレート制限の照合にのみ使う
$ipHash = hash('sha256', ($_SERVER['REMOTE_ADDR'] ?? 'unknown') . '|othello-league-article-like');

try {
    $insStmt = $pdo->prepare(
        "INSERT INTO article_likes (article_id, ip_hash, liked_date) VALUES (:id, :ip, CURDATE())"
    );
    $insStmt->execute(['id' => $articleId, 'ip' => $ipHash]);
} catch (\PDOException $e) {
    // 一意制約違反＝本日すでにいいね済み
    if ($e->getCode() === '23000') {
        echo json_encode([
            'success' => true, 'already_liked' => true,
            'like_count' => fetch_like_count($pdo, $articleId),
        ], JSON_UNESCAPED_UNICODE);
        exit;
    }
    throw $e;
}

echo json_encode([
    'success' => true, 'already_liked' => false,
    'like_count' => fetch_like_count($pdo, $articleId),
], JSON_UNESCAPED_UNICODE);
