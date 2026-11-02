"""ファンサイト用の軽量版season_state.jsonを作る（generate_news.ymlのデプロイで使う）。
元のseason_state.jsonは数十MBあり全ページの読み込みが極端に遅くなるため、ファンサイトが使う部分
（現在の季・タイトル保持者）だけを抜き出す"""
import json
import os
import sys

KEYS = ("current_season", "titleholders")


def main(data_dir, out_path):
    with open(os.path.join(data_dir, "season_state.json"), encoding="utf-8") as f:
        state = json.load(f)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({k: state.get(k) for k in KEYS}, f, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data", sys.argv[2] if len(sys.argv) > 2 else "/tmp/news_out/season_state.json")
