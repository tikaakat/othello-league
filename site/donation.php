<?php
// ============================================================
// 後援会（寄付）：ファンサイトの「後援会」ページから、リーグ全体への寄付を受け付ける（Stripe Checkout）。
// 選手個人への支援（サポーター：create_checkout.php）とは別。
//   ・金額は自由（100円〜50,000円）。見返りなしでも、後援会名簿に名前を載せることもできる（匿名可）
//   ・1,000円以上は「名付け親」になれる：次の奨励会リーグの入会者に、好きな名前（姓・名）を付けられる
// 決済が確定した時点で stripe_webhook.php が donations に記録する（ここではDBに書かない）。
//
// GET  ?action=list                                  後援会名簿（名前を載せる人だけ）と、名付けの結果
// POST {"action":"checkout","amount","display_name","show_name","message","naming":{"family","given","gender"}}
// GET  ?action=pending_names&key=…                    名付けの順番待ち（新人リーグの実行時に取得。key＝取り込み用の鍵）
// POST {"action":"mark_named","key","items":[{"id","season","name"}]}   名付け済みにする
// POST {"action":"admin_list","admin_key"}            運営用：全件（金額つき）
// POST {"action":"reject_name","admin_key","id"}      運営用：名付けを取り消す（実在の有名人の名前など。返金は手動）
// ============================================================

ini_set('display_errors', '0');
error_reporting(E_ALL);

require_once __DIR__ . '/db_config.php';
require_once __DIR__ . '/admin_auth.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

const DONATION_MIN = 100;
const DONATION_MAX = 50000;
const NAMING_MIN = 1000;
const NAME_NG_WORDS = ['死', '殺', 'うんこ', 'ちんこ', 'まんこ', '糞', '犯', '淫'];

function out($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function import_key_ok($key) {
    // 取り込み用の鍵（GitHub Actionsの秘密情報 IMPORT_SECRET_KEY）。デプロイ時に league_secrets.php へハッシュだけを書き出す
    if (!is_file(__DIR__ . '/league_secrets.php')) return false;
    require_once __DIR__ . '/league_secrets.php';
    return is_string($key) && $key !== '' && defined('IMPORT_KEY_SHA256') && hash_equals(IMPORT_KEY_SHA256, hash('sha256', $key));
}

$pdo = league_db_connect();
$pdo->exec("CREATE TABLE IF NOT EXISTS donations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    stripe_session_id VARCHAR(255) NOT NULL UNIQUE,
    amount_jpy INT NOT NULL,
    display_name VARCHAR(30) NULL,
    show_name TINYINT NOT NULL DEFAULT 0,
    message VARCHAR(100) NULL,
    naming_family VARCHAR(10) NULL,
    naming_given VARCHAR(10) NULL,
    naming_gender VARCHAR(8) NULL,
    naming_status VARCHAR(12) NULL,
    named_season INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4");

$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET') {
    $action = $_GET['action'] ?? 'list';
    if ($action === 'list') {
        $rows = $pdo->query("SELECT display_name, show_name, message, naming_family, naming_given, naming_status, named_season, created_at
                             FROM donations ORDER BY id DESC LIMIT 300")->fetchAll();
        $members = []; $namings = []; $anon = 0;
        foreach ($rows as $r) {
            $name = ((int)$r['show_name'] && $r['display_name']) ? $r['display_name'] : null;
            if ($name === null) $anon++;
            else $members[] = ['name' => $name, 'message' => $r['message'], 'date' => substr($r['created_at'], 0, 10)];
            if ($r['naming_family'] && $r['naming_status'] !== 'rejected') {
                $namings[] = [
                    'named' => $r['naming_family'] . $r['naming_given'],
                    'by' => $name ?? '匿名の後援会員',
                    'status' => $r['naming_status'], 'season' => $r['named_season'] !== null ? (int)$r['named_season'] : null,
                ];
            }
        }
        out(['members' => $members, 'anonymous_count' => $anon, 'total_count' => count($rows), 'namings' => $namings]);
    }
    if ($action === 'pending_names') {
        if (!import_key_ok($_GET['key'] ?? '')) out(['error' => 'forbidden'], 403);
        $rows = $pdo->query("SELECT id, naming_family AS family, naming_given AS given, naming_gender AS gender, display_name, show_name
                             FROM donations WHERE naming_status = 'pending' ORDER BY id ASC")->fetchAll();
        $names = array_map(fn($r) => [
            'id' => (int)$r['id'], 'family' => $r['family'], 'given' => $r['given'], 'gender' => $r['gender'],
            'by' => ((int)$r['show_name'] && $r['display_name']) ? $r['display_name'] : null,
        ], $rows);
        out(['names' => $names]);
    }
    out(['error' => '不明な操作です'], 400);
}

if ($method !== 'POST') out(['error' => 'GETかPOSTのみ対応しています'], 405);
$body = json_decode(file_get_contents('php://input'), true) ?? [];
$action = (string)($body['action'] ?? '');

if ($action === 'mark_named') {
    if (!import_key_ok($body['key'] ?? '')) out(['error' => 'forbidden'], 403);
    $st = $pdo->prepare("UPDATE donations SET naming_status = 'named', named_season = :s WHERE id = :id AND naming_status = 'pending'");
    foreach (($body['items'] ?? []) as $it) {
        $st->execute(['s' => (int)($it['season'] ?? 0), 'id' => (int)($it['id'] ?? 0)]);
    }
    out(['success' => true]);
}

if ($action === 'admin_list') {
    if (!is_admin_key($body['admin_key'] ?? '')) out(['error' => '合言葉が違います'], 403);
    out(['donations' => $pdo->query("SELECT * FROM donations ORDER BY id DESC LIMIT 300")->fetchAll()]);
}

if ($action === 'reject_name') {
    if (!is_admin_key($body['admin_key'] ?? '')) out(['error' => '合言葉が違います'], 403);
    $pdo->prepare("UPDATE donations SET naming_status = 'rejected' WHERE id = :id AND naming_status = 'pending'")
        ->execute(['id' => (int)($body['id'] ?? 0)]);
    out(['success' => true]);
}

if ($action !== 'checkout') out(['error' => '不明な操作です'], 400);

$amount = (int)($body['amount'] ?? 0);
if ($amount < DONATION_MIN || $amount > DONATION_MAX) out(['error' => '金額は100円〜50,000円で指定してください'], 400);
$displayName = trim((string)($body['display_name'] ?? ''));
if (mb_strlen($displayName) > 30) out(['error' => 'お名前は30文字以内にしてください'], 400);
$showName = !empty($body['show_name']) && $displayName !== '';
$message = trim((string)($body['message'] ?? ''));
if (mb_strlen($message) > 100) out(['error' => 'ひとことは100文字以内にしてください'], 400);
if (stripos($displayName . $message, 'http') !== false) out(['error' => 'リンク（URL）は書けません'], 400);

$naming = $body['naming'] ?? null;
$family = $given = $gender = '';
if (is_array($naming) && (trim((string)($naming['family'] ?? '')) !== '' || trim((string)($naming['given'] ?? '')) !== '')) {
    if ($amount < NAMING_MIN) out(['error' => '名付け親は1,000円以上で承ります'], 400);
    $family = trim((string)($naming['family'] ?? ''));
    $given = trim((string)($naming['given'] ?? ''));
    $gender = ($naming['gender'] ?? '') === 'female' ? 'female' : 'male';
    $re = '/^[\p{Han}\p{Hiragana}\p{Katakana}ー々]{1,5}$/u';
    if (!preg_match($re, $family) || !preg_match($re, $given)) {
        out(['error' => '名前は姓・名それぞれ1〜5文字の漢字・ひらがな・カタカナで入力してください'], 400);
    }
    foreach (NAME_NG_WORDS as $w) {
        if (mb_strpos($family . $given, $w) !== false) out(['error' => 'その名前は付けられません'], 400);
    }
    $full = $family . $given;
    $dup = $pdo->prepare("SELECT 1 FROM individuals WHERE display_name = :n
                          UNION ALL SELECT 1 FROM retired_archive WHERE display_name = :n2
                          UNION ALL SELECT 1 FROM donations WHERE CONCAT(naming_family, naming_given) = :n3 AND naming_status <> 'rejected' LIMIT 1");
    $dup->execute(['n' => $full, 'n2' => $full, 'n3' => $full]);
    if ($dup->fetchColumn()) out(['error' => '同じ名前の選手が既にいます。別の名前にしてください'], 409);
}

require_once __DIR__ . '/stripe_config.php';
require_once __DIR__ . '/stripe_client.php';

$newsBase = 'https://lab.spia-net.com/othello-news/';
$params = [
    'mode' => 'payment',
    'submit_type' => 'donate',
    'locale' => 'ja',
    'success_url' => $newsBase . '?donation=success#support',
    'cancel_url' => $newsBase . '?donation=cancel#support',
    'line_items' => [[
        'quantity' => 1,
        'price_data' => [
            'currency' => 'jpy',
            'unit_amount' => $amount,
            'product_data' => ['name' => $family !== '' ? 'オセロ界タイムズ後援会（名付け親）' : 'オセロ界タイムズ後援会への寄付'],
        ],
    ]],
    'metadata' => [
        'kind' => 'donation',
        'display_name' => $displayName,
        'show_name' => $showName ? '1' : '0',
        'message' => $message,
        'naming_family' => $family,
        'naming_given' => $given,
        'naming_gender' => $gender,
    ],
];
[$ok, $result] = stripe_api_request('POST', '/v1/checkout/sessions', $params);
if (!$ok || empty($result['url'])) out(['error' => '決済ページの作成に失敗しました'], 502);
out(['url' => $result['url']]);
