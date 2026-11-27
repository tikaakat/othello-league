<?php
// 結果ページを書き出すときの、一時的なMySQLへの接続（GitHub Actions の中だけで使う。本番の接続情報ではない）
function league_db_connect() {
    $host = getenv('DB_HOST') ?: '127.0.0.1';
    $name = getenv('DB_NAME') ?: 'othello';
    $pdo = new PDO("mysql:host={$host};dbname={$name};charset=utf8mb4", getenv('DB_USER') ?: 'league', getenv('DB_PASS') ?: 'league', [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => true,
    ]);
    $pdo->exec("SET time_zone = '+09:00'");
    return $pdo;
}
