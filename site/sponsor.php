<?php
// ============================================================
// 個体への有料支援（スポンサー）機能。
// このファイルは現時点ではGET（一覧取得）のみを提供する。
// 実際の決済（Stripe Checkout）・Webhook受信によるsponsorsテーブルへの書き込みは
// 別途追加する（見た目を先行実装している段階のため、書き込みエンドポイントはまだ無い）。
// DBマイグレーションはdeploy_site.ymlのrun_migration=add_sponsor_featureで適用済みが前提。
//
// GET：個体への支援者一覧を取得する（表示名・一言のみ。金額は返さない）
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'GETのみ対応しています']);
    exit;
}

$individualId = trim((string)($_GET['individual_id'] ?? ''));
if ($individualId === '' || mb_strlen($individualId) > 32) {
    http_response_code(400);
    echo json_encode(['error' => 'individual_id が不正です']);
    exit;
}

$pdo = league_db_connect();
$limit = min(50, max(1, (int)($_GET['limit'] ?? 20)));

$stmt = $pdo->prepare(
    "SELECT display_name, message FROM sponsors
     WHERE individual_id = :id ORDER BY created_at DESC LIMIT :limit"
);
$stmt->bindValue(':id', $individualId);
$stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
$stmt->execute();

echo json_encode(['sponsors' => $stmt->fetchAll()], JSON_UNESCAPED_UNICODE);
