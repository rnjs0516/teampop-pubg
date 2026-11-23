# -*- coding: utf-8 -*-
"""하이라이트 클립을 highlights.json 에 추가/확인/삭제합니다.

  python clip.py "https://youtu.be/XXXX" -p gunerz_ -t "1v4 클러치"
      -> gunerz_ 의 클립으로 등록. 멤버당 3개까지 유지하고, 넘으면 그 멤버의
         가장 오래된 클립을 빼고 알려줍니다.
  python clip.py "https://youtu.be/XXXX" -p gunerz_ -t "1v4 클러치" -m 3
      -> (선택) --matches 목록의 3번 경기에 연결 - 그 경기 카드에 ▶ 버튼이 붙습니다
  python clip.py --matches          최근 경기 목록 보기
  python clip.py --list             등록된 클립 보기
  python clip.py --remove 2         2번 클립 삭제

url 의 유효성은 build.py 가 빌드할 때 검사하고, 못 쓰는 링크는 경고와 함께 빠집니다.
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent
HL = ROOT / "highlights.json"
DATA = ROOT / "docs" / "data.json"
KST = timezone(timedelta(hours=9))
SHOW = 20
KEEP = 3        # clips per member
MAPS = {"Baltic_Main": "에란겔", "Erangel_Main": "에란겔", "Desert_Main": "미라마",
        "Savage_Main": "사녹", "DihorOtok_Main": "비켄디", "Tiger_Main": "태이고",
        "Neon_Main": "론도", "Kiki_Main": "데스턴", "Summerland_Main": "카라킨",
        "Chimera_Main": "파라모", "Heaven_Main": "헤이븐"}
DOW = "월화수목금토일"


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


def matches():
    """Newest first, from the last build. Run update.bat first to see today's games."""
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))["matches"]
    except Exception:
        return []


def kst(iso):
    return (datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
            .replace(tzinfo=timezone.utc).astimezone(KST))


def label(m):
    t = kst(m["at"])
    who = ", ".join(sorted(m.get("members") or {}))
    return "%s(%s) %s  %-4s %3s위  %s" % (t.strftime("%m/%d"), DOW[t.weekday()], t.strftime("%H:%M"),
                                       MAPS.get(m.get("map"), m.get("map") or "?"),
                                       m.get("rank") if m.get("rank") is not None else "-", who)


def show_matches(ms):
    if not ms:
        print("경기 데이터가 없습니다. 먼저 update.bat 을 실행하세요.")
        return
    for i, m in enumerate(ms[:SHOW], 1):
        print("%3d. %s" % (i, label(m)))


def show(rows):
    if not rows:
        print("등록된 클립이 없습니다.")
        return
    by_id = {m["id"]: m for m in matches()}
    for i, c in enumerate(rows, 1):
        m = by_id.get(c.get("match") or "")
        print("%2d. [%s] %s  %s" % (i, c.get("player") or "-", c.get("title") or "-",
                                    label(m) if m else (c.get("date") or "")))
        print("    %s" % (c.get("url") or ""))


ap = argparse.ArgumentParser(add_help=True)
ap.add_argument("url", nargs="?", help="유튜브 링크, 또는 clips/파일명.mp4")
ap.add_argument("-p", "--player", default="", help="누구 장면인지 (닉네임)")
ap.add_argument("-t", "--title", default="", help="제목")
ap.add_argument("-m", "--match", type=int, metavar="N", help="--matches 목록의 N번 경기 (0 이면 경기 없음)")
ap.add_argument("-d", "--date", default="", help="날짜 (기본: 고른 경기의 날짜, 없으면 오늘)")
ap.add_argument("-n", "--note", default="", help="한 줄 메모")
ap.add_argument("--matches", action="store_true", help="최근 경기 목록 보기")
ap.add_argument("--list", action="store_true", help="등록된 클립 보기")
ap.add_argument("--remove", type=int, metavar="N", help="N번 클립 삭제 (--list 의 번호)")
a = ap.parse_args()

rows = load()

if a.matches:
    show_matches(matches())
    sys.exit(0)

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

if not a.player.strip():
    sys.exit("누구의 클립인지 -p 로 적어주세요. 예: -p gunerz_")
squad = []
try:
    squad = json.loads(DATA.read_text(encoding="utf-8")).get("squad") or []
except Exception:
    pass
if squad and a.player not in squad:
    print("참고: %s 는 스쿼드 명단(%s)에 없습니다. 그대로 등록합니다." % (a.player, ", ".join(squad)))

ms = matches()
pick = a.match
m = None
if pick:
    if not 1 <= pick <= min(SHOW, len(ms)):
        sys.exit("1 ~ %d 사이의 경기 번호를 쓰세요." % min(SHOW, len(ms)))
    m = ms[pick - 1]
    if a.player and a.player not in (m.get("members") or {}):
        print("참고: %s 는 이 경기 멤버 목록(%s)에 없습니다. 그대로 등록합니다."
              % (a.player, ", ".join(sorted(m.get("members") or {}))))

row = {"player": a.player, "title": a.title or "하이라이트", "url": a.url,
       "date": a.date or (kst(m["at"]).strftime("%Y-%m-%d") if m else datetime.now(KST).strftime("%Y-%m-%d")),
       "note": a.note}
if m:
    row["match"] = m["id"]
rows.append(row)
# keep the newest KEEP per member: drop that member's oldest beyond the cap
mine = [i for i, c in enumerate(rows) if c.get("player") == a.player]
mine.sort(key=lambda i: (rows[i].get("date") or "", i))
for i in sorted(mine[:max(0, len(mine) - KEEP)], reverse=True):
    print("%s 의 클립이 %d개를 넘어 가장 오래된 것을 뺐습니다: %s (%s)"
          % (a.player, KEEP, rows[i].get("title"), rows[i].get("date")))
    rows.pop(i)
save(rows)
print("\n추가했습니다. 현재 %d개 (%s %d개)." % (len(rows), a.player, min(len(mine), KEEP)))
show(rows)
print("\n이제 update.bat 을 실행하면 페이지에 반영되고 푸시됩니다.")
