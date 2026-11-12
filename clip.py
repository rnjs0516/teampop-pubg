# -*- coding: utf-8 -*-
"""하이라이트 클립을 highlights.json 에 추가/확인/삭제합니다.

  python clip.py "https://youtu.be/XXXX" -p gunerz_ -t "1v4 클러치"
  python clip.py --list
  python clip.py --remove 2

url 의 유효성은 build.py 가 빌드할 때 검사하고, 못 쓰는 링크는 경고와 함께 빠집니다.
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone, timedelta

HL = pathlib.Path(__file__).resolve().parent / "highlights.json"
KST = timezone(timedelta(hours=9))


def load():
    if not HL.exists():
        return []
    try:
        raw = json.loads(HL.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit("highlights.json 을 읽지 못했습니다: %s\n파일을 고치거나 지우고 다시 시도하세요." % e)
    rows = raw.get("clips") if isinstance(raw, dict) else raw
    return rows if isinstance(rows, list) else []


def save(rows):
    HL.write_text(json.dumps({"clips": rows}, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8")


def show(rows):
    if not rows:
        print("등록된 클립이 없습니다.")
        return
    for i, c in enumerate(rows, 1):
        print("%2d. [%s] %s  %s" % (i, c.get("player") or "-", c.get("title") or "-",
                                    c.get("date") or ""))
        print("    %s" % (c.get("url") or ""))


ap = argparse.ArgumentParser(add_help=True)
ap.add_argument("url", nargs="?", help="유튜브 링크, 또는 clips/파일명.mp4")
ap.add_argument("-p", "--player", default="", help="올린 사람 닉네임")
ap.add_argument("-t", "--title", default="", help="제목")
ap.add_argument("-d", "--date", default="", help="날짜 (기본: 오늘)")
ap.add_argument("-n", "--note", default="", help="한 줄 메모")
ap.add_argument("--list", action="store_true", help="등록된 클립 보기")
ap.add_argument("--remove", type=int, metavar="N", help="N번 클립 삭제 (--list 의 번호)")
a = ap.parse_args()

rows = load()

if a.list:
    show(rows)
    sys.exit(0)

if a.remove is not None:
    if not 1 <= a.remove <= len(rows):
        sys.exit("1 ~ %d 사이의 번호를 쓰세요. 현재 %d개 있습니다." % (len(rows), len(rows)))
    gone = rows.pop(a.remove - 1)
    save(rows)
    print("삭제: %s" % (gone.get("title") or gone.get("url")))
    show(rows)
    sys.exit(0)

if not a.url:
    ap.print_help()
    print()
    show(rows)
    sys.exit(0)

rows.append({"player": a.player, "title": a.title or "하이라이트",
             "url": a.url, "date": a.date or datetime.now(KST).strftime("%Y-%m-%d"),
             "note": a.note})
save(rows)
print("추가했습니다. 현재 %d개." % len(rows))
show(rows)
print("\n이제 update.bat 을 실행하면 페이지에 반영되고 푸시됩니다.")
