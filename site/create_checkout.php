<?php
// ============================================================
// 選手個人への有料支援（サポーター）は終了した。寄付はサイト全体の運営への後援会（donation.php）のみ受け付ける。
// 古いページや外部から呼ばれても決済を作らないよう、常に「終了」を返す。
// 過去の支援の記録（sponsors・permanent_sponsorsテーブル）はDBに残している。
// ============================================================

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
http_response_code(410);
echo json_encode(['error' => '選手個人への支援の受付は終了しました。リーグ全体への寄付は後援会のページからお願いします。'], JSON_UNESCAPED_UNICODE);
