import random

# 日本の一般的な名字リスト（重複しても連番で区別するので、量はそこそこあれば十分）
SURNAMES = [
    "佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤",
    "吉田", "山田", "佐々木", "山口", "松本", "井上", "木村", "林", "斎藤", "清水",
    "山崎", "森", "阿部", "池田", "橋本", "山下", "石川", "中島", "前田", "藤田",
    "後藤", "小川", "岡田", "村上", "長谷川", "近藤", "石井", "坂本", "遠藤", "青木",
    "藤井", "西村", "福田", "太田", "三浦", "岡本", "松田", "中川", "中野", "原田",
    "小野", "田村", "竹内", "金子", "和田", "中山", "石田", "上田", "森田", "柴田",
    "酒井", "工藤", "横山", "宮崎", "宮本", "内田", "高木", "安藤", "谷口", "大野",
]


class NameRegistry:
    """
    名字の重複を管理し、「佐藤」「佐藤2」「佐藤3」のように連番を振る。
    複数プロセス（世代ごとの実行）をまたいで一貫させるため、
    カウント状態を辞書として外部（training_state等）に保存・復元できるようにしてある。
    """
    def __init__(self, counts=None):
        self.counts = dict(counts) if counts else {}

    def generate(self):
        surname = random.choice(SURNAMES)
        self.counts[surname] = self.counts.get(surname, 0) + 1
        n = self.counts[surname]
        return surname if n == 1 else f"{surname}{n}"

    def to_dict(self):
        return dict(self.counts)

    @staticmethod
    def from_dict(d):
        return NameRegistry(d or {})
