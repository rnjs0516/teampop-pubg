"""Build the full squad seed: no artificial match cap, every ranked squad game the
API still returns (its player->matches relationship reaches back about 14 days).

Rate-limited calls: /players (1) + /seasons (1) + /ranked per member (4) = 6.
/matches and the telemetry CDN are exempt and are fetched concurrently.
"""
import json, os, hashlib, urllib.request, urllib.error, urllib.parse, pathlib, sys, gzip, time, collections
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))   # GitHub Actions runs in UTC; the page must read KST
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent
ENV = ROOT / ".env"
HL = ROOT / "highlights.json"
TEMPLATE = ROOT / "template.html"
OUT_HTML = ROOT / "docs" / "index.html"
OUT_JSON = ROOT / "docs" / "data.json"      # the same seed, for anything else that wants it

# 환경변수가 우선 (GitHub Actions 는 secrets 로 주입), 없으면 로컬 .env
KEY = os.environ.get("PUBG_API_KEY", "").strip()
if not KEY and ENV.exists():
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("PUBG_API_KEY="):
            KEY = line.split("=", 1)[1].strip()
if not KEY or KEY.startswith("PUT_"):
    sys.exit("PUBG_API_KEY 를 찾지 못했습니다. 환경변수로 넣거나 `.env` 에 적으세요 (%s)" % ENV)


# ---------- highlight clips ----------
# Authored by hand in highlights.json. Two sources work in a browser: a YouTube
# link (iframe) and a plain mp4/webm the page can serve itself. Anything else is
# dropped with a warning rather than written into the page, so a typo cannot put
# an arbitrary URL - or a javascript: scheme - into everyone's dashboard.
YT_HOSTS = {"youtu.be", "www.youtu.be", "youtube.com", "www.youtube.com",
            "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
            "www.youtube-nocookie.com"}


def yt_id(u):
    """Return the 11-char video id, or None when this is not a YouTube link."""
    try:
        q = urllib.parse.urlparse(u)
    except Exception:
        return None
    if q.scheme not in ("http", "https") or q.hostname not in YT_HOSTS:
        return None
    cand = None
    if q.hostname in ("youtu.be", "www.youtu.be"):
        cand = q.path.lstrip("/").split("/")[0]
    else:
        parts = [s for s in q.path.split("/") if s]
        if parts and parts[0] in ("shorts", "embed", "live", "v"):
            cand = parts[1] if len(parts) > 1 else None
        else:
            cand = (urllib.parse.parse_qs(q.query).get("v") or [None])[0]
    if cand and len(cand) == 11 and all(c.isalnum() or c in "_-" for c in cand):
        return cand
    return None


def load_highlights():
    if not HL.exists():
        return []
    try:
        raw = json.loads(HL.read_text(encoding="utf-8"))
    except Exception as e:
        print("highlights.json 을 읽지 못했습니다 (%s) - 하이라이트 없이 빌드합니다" % e, file=sys.stderr)
        return []
    rows = raw.get("clips") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        print("highlights.json 형식이 올바르지 않습니다 - clips 배열이 필요합니다", file=sys.stderr)
        return []

    out, skipped = [], 0
    for i, c in enumerate(rows):
        if not isinstance(c, dict):
            skipped += 1
            continue
        url = str(c.get("url") or "").strip()
        if not url:
            skipped += 1
            continue
        item = {"player": str(c.get("player") or "").strip(),
                "title": str(c.get("title") or "").strip() or "하이라이트",
                "date": str(c.get("date") or "").strip(),
                "note": str(c.get("note") or "").strip(),
                # match id the clip was recorded from (clip.py fills it); optional
                "match": str(c.get("match") or "").strip()}
        vid = yt_id(url)
        if vid:
            item.update(kind="yt", id=vid)
        elif url.startswith("clips/") and ".." not in url:
            if not (OUT_HTML.parent / url).exists():
                print("  클립 파일이 없습니다: docs/%s - 건너뜁니다" % url, file=sys.stderr)
                skipped += 1
                continue
            if not url.lower().endswith((".mp4", ".webm")):
                print("  브라우저가 재생 못 하는 확장자입니다: %s (mp4/webm 만) - 건너뜁니다" % url, file=sys.stderr)
                skipped += 1
                continue
            item.update(kind="file", url=url)
        elif url.lower().split("?")[0].endswith((".mp4", ".webm")) and url.startswith("https://"):
            item.update(kind="file", url=url)
        else:
            print("  지원하지 않는 url 입니다: %s" % url[:80], file=sys.stderr)
            print("    유튜브 링크, 또는 mp4/webm 파일만 됩니다.", file=sys.stderr)
            skipped += 1
            continue
        out.append(item)

    out.sort(key=lambda c: c.get("date") or "", reverse=True)
    print("하이라이트 %d개%s" % (len(out), " (%d개 건너뜀)" % skipped if skipped else ""), file=sys.stderr)
    return out


SHARD, MODE, MATCH_TYPE = "kakao", "squad", "competitive"
SQUAD = ["gunerz_", "DDalGi_GOD", "nM_-ShinyaT", "Moontari_"]
BASE = "https://api.pubg.com/shards/" + SHARD
H = {"Authorization": "Bearer " + KEY, "Accept": "application/vnd.api+json", "Accept-Encoding": "gzip"}
NOT_GROUND = {"TransportAircraft", "Aircraft", "EmergencyPickup", "Glider", "MotorGlider", "Parachute"}
AIRCRAFT = {"TransportAircraft", "Aircraft"}
FLYRIDE = {"EmergencyPickup", "Glider", "MotorGlider"}
THROWABLE = {"ProjGrenade_C": "수류탄", "ProjMolotov_C": "화염병", "ProjC4_C": "C4",
             "ProjStickyGrenade_C": "점착 폭탄", "ProjBluezoneGrenade_C": "블루존 수류탄",
             "ProjSpawnMolotovFireDebuff_C": "화염병"}
WEAPONS = {
    "WeapHK416_C": "M416", "WeapAUG_C": "AUG", "WeapM16A4_C": "M16A4", "WeapACE32_C": "ACE32",
    "WeapBerylM762_C": "Beryl M762", "WeapAK47_C": "AKM", "WeapG36C_C": "G36C",
    "WeapSCAR-L_C": "SCAR-L", "WeapQBZ95_C": "QBZ95", "WeapMk47Mutant_C": "Mk47",
    "WeapGroza_C": "Groza", "WeapFNFal_C": "SLR", "WeapMk14_C": "Mk14", "WeapMk12_C": "Mk12",
    "WeapMini14_C": "Mini 14", "WeapSKS_C": "SKS", "WeapQBU88_C": "QBU", "WeapVSS_C": "VSS",
    "WeapKar98k_C": "Kar98k", "WeapM24_C": "M24", "WeapAWM_C": "AWM", "WeapWin1894_C": "Win94",
    "WeapMosinNagant_C": "Mosin", "WeapLynxAMR_C": "Lynx AMR", "WeapUZI_C": "Micro UZI",
    "WeapUMP_C": "UMP45", "WeapVector_C": "Vector", "WeapThompson_C": "Tommy Gun",
    "WeapMP5K_C": "MP5K", "WeapBizonPP19_C": "PP-19", "WeapP90_C": "P90", "WeapMP9_C": "MP9",
    "WeapDP28_C": "DP-28", "WeapM249_C": "M249", "WeapMG3_C": "MG3", "WeapBerreta686_C": "S686",
    "WeapWinchester_C": "S1897", "WeapSaiga12_C": "S12K", "WeapDP12_C": "DBS",
    "WeapSawnoff_C": "Sawed-off", "WeapOriginS12_C": "O12", "WeapM1911_C": "P1911",
    "WeapM9_C": "P92", "WeapNagantRevolver_C": "R1895", "WeapRhino_C": "R45",
    "WeapG18_C": "P18C", "WeapSkorpion_C": "Skorpion", "WeapDesertEagle_C": "Deagle",
    "WeapCrossbow_1_C": "석궁", "WeapPan_C": "프라이팬", "WeapMachete_C": "마체테", "WeapM79_C": "M79",
}
KEEP = ["kills", "assists", "DBNOs", "damageDealt", "winPlace", "timeSurvived", "headshotKills",
        "longestKill", "revives", "deathType", "walkDistance", "rideDistance", "swimDistance",
        "weaponsAcquired", "heals", "boosts", "killPlace"]


def weap_label(raw):
    if not raw:
        return "기타"
    if raw in THROWABLE:
        return THROWABLE[raw]
    if raw in WEAPONS:
        return WEAPONS[raw]
    s = raw
    for pre in ("Weap", "Proj", "BP_"):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.replace("_C", "").replace("_", " ")


def ts(d):
    try:
        return datetime.strptime(d[:23].ljust(23, "0"), "%Y-%m-%dT%H:%M:%S.%f").timestamp()
    except Exception:
        return None


def rnd(v, n=1):
    return round(v, n) if isinstance(v, float) else v


def fetch(url, headers, tries=5):
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=240) as r:
                raw = r.read()
                return gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
        except urllib.error.HTTPError as e:
            if e.code == 401:
                sys.exit("PUBG API 키 인증 실패(401). 키가 만료됐거나 잘못됐습니다. "
                         "GitHub Actions 라면 저장소 Secrets 의 PUBG_API_KEY 를 확인하세요.")
            if e.code == 429 and n < tries - 1:
                reset = e.headers.get("X-Ratelimit-Reset")
                w = max(3, min(70, int(reset) - int(time.time()) + 2)) if reset and reset.isdigit() else 12
                print("  429 -> wait %ds" % w, file=sys.stderr)
                time.sleep(w)
                continue
            raise
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(2 + 2 * n)


def norm_ranked(st):
    st = st or {}
    r = int(st.get("roundsPlayed") or 0)
    cur, best = st.get("currentTier") or {}, st.get("bestTier") or {}
    return {"rounds": r, "kills": int(st.get("kills") or 0), "deaths": int(st.get("deaths") or 0),
            "wins": int(st.get("wins") or 0),
            "top10s": int(round(float(st.get("top10Ratio") or 0) * r)),
            "damage": rnd(float(st.get("damageDealt") or 0)),
            "hsKills": int(st.get("headshotKills") or 0),
            "assists": int(st.get("assists") or 0), "dBNOs": int(st.get("dBNOs") or 0),
            "revives": int(st.get("revives") or 0),
            "avgRank": rnd(float(st["avgRank"]), 2) if st.get("avgRank") else None,
            "tier": cur.get("tier"), "subTier": cur.get("subTier"),
            "bestTier": best.get("tier"), "bestSubTier": best.get("subTier"),
            "rp": int(st.get("currentRankPoint") or 0), "bestRp": int(st.get("bestRankPoint") or 0)}


# ---- 1 rate-limited call for all four members, no slice on the match list ----
pl = json.loads(fetch(BASE + "/players?filter[playerNames]=" +
                      ",".join(urllib.parse.quote(n, safe="") for n in SQUAD), H))
found, union = [], []
for p in pl["data"]:
    found.append({"name": p["attributes"]["name"], "accountId": p["id"]})
    for m in p["relationships"]["matches"]["data"]:
        if m["id"] not in union:
            union.append(m["id"])
NAME = {f["accountId"]: f["name"] for f in found}
ACCT = {f["name"]: f["accountId"] for f in found}
print("members %s | union %d matches" % ([f["name"] for f in found], len(union)), file=sys.stderr)

# 새 경기가 없으면 여기서 끝낸다 - 제한 대상 호출 1회만 쓰고 종료하므로 자주 돌려도 싸다
hl_clips = load_highlights()
hl_sig = hashlib.sha1(json.dumps(hl_clips, sort_keys=True, ensure_ascii=False)
                      .encode("utf-8")).hexdigest()[:12]
union_sig = hashlib.sha1((",".join(sorted(union)) + "|hl:" + hl_sig)
                         .encode("utf-8")).hexdigest()
if "--force" not in sys.argv and OUT_JSON.exists():
    try:
        _prev = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        prev, prev_pending = _prev.get("unionSig"), int(_prev.get("foesPending") or 0)
    except Exception:
        prev, prev_pending = None, 0
    if prev_pending:
        print("상대 조회가 %d명 남아 있어 계속 진행합니다" % prev_pending, file=sys.stderr)
    if prev == union_sig and not prev_pending:
        print("새 경기도 없고 하이라이트도 그대로 - 빌드 생략 (호출 1회 사용)", file=sys.stderr)
        sys.exit(0)

cur_season = next(s["id"] for s in json.loads(fetch(BASE + "/seasons", H))["data"]
                  if s["attributes"].get("isCurrentSeason"))
ranked = {}
for f in found:
    try:
        d = json.loads(fetch(BASE + "/players/%s/seasons/%s/ranked" % (f["accountId"], cur_season), H))
        ranked[f["accountId"]] = (d["data"]["attributes"].get("rankedGameModeStats") or {}).get(MODE, {})
    except Exception as e:
        print("  ranked fail %s %s" % (f["name"], e), file=sys.stderr)
        ranked[f["accountId"]] = {}


def analyse(mid):
    """Returns the trimmed match dict, or None when it is not a squad game for this crew."""
    m = json.loads(fetch(BASE + "/matches/" + mid, H))
    attrs, inc = m["data"]["attributes"], m["included"]
    if attrs.get("gameMode") != MODE or attrs.get("matchType") != MATCH_TYPE:
        return None, None
    part = {it["id"]: it["attributes"]["stats"] for it in inc if it.get("type") == "participant"}
    best, rosters = None, 0
    for it in inc:
        if it.get("type") != "roster":
            continue
        rosters += 1
        pids = [d["id"] for d in it.get("relationships", {}).get("participants", {}).get("data", [])]
        mem = [p for p in pids if part.get(p, {}).get("playerId") in NAME]
        if mem and (best is None or len(mem) > len(best[2])):
            best = (it, pids, mem)
    if not best or len(best[2]) < 2:
        return None, None
    roster, pids, mem = best
    accts = {part[p]["playerId"] for p in mem}
    members = {NAME[part[p]["playerId"]]: {k: rnd(part[p].get(k)) for k in KEEP} for p in mem}

    tel = {n: {"clutch": 0, "first": 0, "nade": 0, "tAssist": 0, "revSec": 0.0, "revN": 0,
               "swimSec": 0.0, "swimDist": 0.0, "flySec": 0.0, "flyN": 0,
               "chuteSec": 0.0, "chuteDist": 0.0, "driveSec": 0.0, "driveN": 0,
               "dmgIn": 0.0, "dmgEnemy": 0.0}
           for n in members}
    weapons = {n: collections.Counter() for n in members}
    foes = collections.OrderedDict()   # accountId -> {"name", "knock": [...], "kill": [...]}

    def foe(att, vic, what):
        """att hurt squad member vic. Teammates (same teamId) and the zone never count."""
        aid, vid = att.get("accountId"), vic.get("accountId")
        if vid not in accts or not aid or aid in accts or att.get("teamId") == vic.get("teamId"):
            return
        f = foes.setdefault(aid, {"id": aid, "name": att.get("name") or "?", "knock": [], "kill": []})
        if NAME[vid] not in f[what]:
            f[what].append(NAME[vid])

    url = next((i["attributes"]["URL"] for i in inc
                if i.get("type") == "asset" and i["attributes"].get("name") == "telemetry"), None)
    if url:
        ev = json.loads(fetch(url, {"Accept-Encoding": "gzip", "Accept": "application/json"}))
        ev.sort(key=lambda e: e.get("_D") or "")
        state = {a: "alive" for a in accts}
        groggy, swim_open, air_exit, ride_open, drive_open = {}, {}, {}, {}, {}
        first_done = False

        def close_drive(a, t1):
            d = drive_open.pop(a, None)
            if d and d[1] == 0 and d[0] and t1 and 0 <= t1 - d[0] <= 2400:
                tel[NAME[a]]["driveSec"] += t1 - d[0]
                tel[NAME[a]]["driveN"] += 1

        for e in ev:
            T = e.get("_T")
            if T == "LogSwimStart":
                a = (e.get("character") or {}).get("accountId")
                if a in accts:
                    swim_open[a] = ts(e.get("_D"))
            elif T == "LogSwimEnd":
                a = (e.get("character") or {}).get("accountId")
                if a in accts:
                    tel[NAME[a]]["swimDist"] += float(e.get("swimDistance") or 0)
                    t0, t1 = swim_open.pop(a, None), ts(e.get("_D"))
                    if t0 and t1 and 0 <= t1 - t0 <= 1800:
                        tel[NAME[a]]["swimSec"] += t1 - t0
            elif T == "LogVehicleRide":
                a = (e.get("character") or {}).get("accountId")
                if a in accts:
                    vt = (e.get("vehicle") or {}).get("vehicleType")
                    if vt in FLYRIDE:
                        ride_open[a] = ts(e.get("_D"))
                    elif vt not in AIRCRAFT:
                        seat = e.get("seatIndex")
                        if seat is None:
                            seat = (e.get("vehicle") or {}).get("seatIndex")
                        drive_open[a] = (ts(e.get("_D")), seat)
            elif T == "LogVehicleLeave":
                a = (e.get("character") or {}).get("accountId")
                if a in accts:
                    vt = (e.get("vehicle") or {}).get("vehicleType")
                    if vt in AIRCRAFT:
                        air_exit[a] = ts(e.get("_D"))
                    elif vt in FLYRIDE:
                        t0, t1 = ride_open.pop(a, None), ts(e.get("_D"))
                        if t0 and t1 and 0 <= t1 - t0 <= 1800:
                            tel[NAME[a]]["flySec"] += t1 - t0
                            tel[NAME[a]]["flyN"] += 1
                    else:
                        close_drive(a, ts(e.get("_D")))
            elif T == "LogParachuteLanding":
                a = (e.get("character") or {}).get("accountId")
                if a in accts:
                    tel[NAME[a]]["chuteDist"] += float(e.get("distance") or 0)
                    t0, t1 = air_exit.pop(a, None), ts(e.get("_D"))
                    if t0 and t1 and 0 <= t1 - t0 <= 600:
                        tel[NAME[a]]["chuteSec"] += t1 - t0
            elif T == "LogPlayerTakeDamage":
                vic = e.get("victim") or {}
                a = vic.get("accountId")
                # damage is clamped to remaining HP; a knocked player is always logged 0
                if a in accts and not vic.get("isDBNO"):
                    dmg = float(e.get("damage") or 0)
                    tel[NAME[a]]["dmgIn"] += dmg
                    att = e.get("attacker") or {}
                    if att.get("accountId") and att.get("accountId") != a \
                            and att.get("teamId") != vic.get("teamId"):
                        tel[NAME[a]]["dmgEnemy"] += dmg
            elif T == "LogPlayerMakeGroggy":
                foe(e.get("attacker") or {}, e.get("victim") or {}, "knock")
                v = (e.get("victim") or {}).get("accountId")
                if v in state:
                    state[v] = "groggy"
                if e.get("dBNOId") is not None:
                    groggy[e["dBNOId"]] = e.get("_D")
            elif T == "LogPlayerRevive":
                v = (e.get("victim") or {}).get("accountId")
                if v in state:
                    state[v] = "alive"
                rv = (e.get("reviver") or {}).get("accountId")
                did = e.get("dBNOId")
                if rv in accts and did in groggy:
                    t0, t1 = ts(groggy[did]), ts(e.get("_D"))
                    if t0 and t1 and 0 <= t1 - t0 <= 300:
                        tel[NAME[rv]]["revSec"] += t1 - t0
                        tel[NAME[rv]]["revN"] += 1
            elif T == "LogPlayerKillV2":
                foe(e.get("killer") or {}, e.get("victim") or {}, "kill")
                victim = (e.get("victim") or {}).get("accountId")
                killer = (e.get("killer") or {}).get("accountId")
                causer = (e.get("killerDamageInfo") or {}).get("damageCauserName")
                if killer in accts and victim not in accts and not e.get("isSuicide"):
                    nm = NAME[killer]
                    others = [a for a in accts if a != killer]
                    if others and all(state[a] in ("groggy", "dead") for a in others):
                        tel[nm]["clutch"] += 1
                    if not first_done:
                        tel[nm]["first"] = 1
                        first_done = True
                    if causer in THROWABLE:
                        tel[nm]["nade"] += 1
                    if causer:
                        weapons[nm][causer] += 1
                    for a in (e.get("assists_AccountId") or []):
                        if a in accts and a != killer:
                            tel[NAME[a]]["tAssist"] += 1
                    dm = (e.get("dBNOMaker") or {}).get("accountId")
                    if dm in accts and dm != killer:
                        tel[NAME[dm]]["tAssist"] += 1
                if victim in state:
                    state[victim] = "dead"
                    close_drive(victim, ts(e.get("_D")))

    for n in members:
        members[n]["t"] = {k: (round(v, 1) if isinstance(v, float) else v)
                           for k, v in tel[n].items()}
        members[n]["t"]["w"] = dict(weapons[n])     # raw damageCauserName -> kills
    return {"id": mid, "at": attrs.get("createdAt"), "map": attrs.get("mapName"),
            "dur": attrs.get("duration"), "rank": roster["attributes"]["stats"].get("rank"),
            "rosters": rosters, "members": members, "foes": list(foes.values()),
            "randoms": [part[p].get("name") for p in pids
                        if p in part and part[p].get("playerId") not in NAME]}, weapons


print("fetching %d matches + telemetry..." % len(union), file=sys.stderr)
t0 = time.time()
matches, season_weapons, done = [], collections.defaultdict(collections.Counter), 0
with ThreadPoolExecutor(max_workers=5) as ex:
    for res, weaps in ex.map(analyse, union):
        done += 1
        if done % 20 == 0:
            print("  %d/%d" % (done, len(union)), file=sys.stderr)
        if res:
            matches.append(res)
            for nm, c in weaps.items():
                season_weapons[nm].update(c)

matches.sort(key=lambda x: x["at"] or "", reverse=True)

# The API lists only ~14 days of matches. Keep every match an earlier build already
# stored for this season; otherwise the season's first weeks silently fall off.
try:
    _prev_seed = json.loads(OUT_JSON.read_text(encoding="utf-8"))
except Exception:
    _prev_seed = {}
carried = 0
if _prev_seed.get("seasonId") == cur_season:
    _have = {m["id"] for m in matches}
    for m in _prev_seed.get("matches", []):
        if m.get("id") and m["id"] not in _have:
            matches.append(m)
            carried += 1
    matches.sort(key=lambda x: x["at"] or "", reverse=True)
print("API 목록 밖의 이전 경기 %d개 유지" % carried, file=sys.stderr)

# A carried match stored before a newer field existed (per-match weapons, damage
# taken, opponents) is re-analysed while PUBG still serves it - often well past the
# 14-day listing. Once it is gone (404) it is marked and never asked again.
def _stale(m):
    return not m.get("gone") and any("w" not in ((s.get("t") or {})) for s in m["members"].values())


def _redo(m):
    try:
        res, _w = analyse(m["id"])
        return m, res
    except urllib.error.HTTPError as e:
        # 404: the match is gone. 403: the match is still listed but its telemetry
        # file has expired on the CDN - just as permanent, so stop asking either way.
        return m, ("gone" if e.code in (403, 404) else None)
    except Exception:
        return m, None


_old = [m for m in matches if _stale(m)]
if _old:
    redone = gone = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        for m, res in ex.map(_redo, _old):
            if res == "gone":
                m["gone"] = True
                gone += 1
            elif res:
                m.clear()
                m.update(res)
                redone += 1
    print("이전 경기 보강: 재분석 %d개 · PUBG 에서 삭제됨 %d개 · 실패(다음에 재시도) %d개"
          % (redone, gone, len(_old) - redone - gone), file=sys.stderr)

# /seasons carries no start date, so derive the season boundary from the data: for the
# member with the most ranked rounds, their Nth most recent collected match is the
# season's first. Validated when the filtered per-member counts equal roundsPlayed.
per = collections.defaultdict(list)
for m in matches:
    for nm in m["members"]:
        per[nm].append(m["at"])
for nm in per:
    per[nm].sort(reverse=True)
cands = []
for f in found:
    r = int((ranked.get(f["accountId"]) or {}).get("roundsPlayed") or 0)
    lst = per.get(f["name"], [])
    if r and len(lst) >= r:
        cands.append((r, lst[r - 1], f["name"]))
cands.sort(reverse=True)
season_start = cands[0][1] if cands else None
dropped = 0
if season_start:
    before = len(matches)
    matches = [m for m in matches if (m["at"] or "") >= season_start]
    dropped = before - len(matches)
    print("season start %s (from %s) - dropped %d older matches"
          % (season_start, cands[0][2], dropped), file=sys.stderr)

season_weapons = collections.defaultdict(collections.Counter)
for m in matches:
    for nm, s in m["members"].items():
        season_weapons[nm].update((s.get("t") or {}).get("w") or {})

# ---------- opponents: one ranked lookup per player per season ----------
# Looked up once, shortly after the match, and kept in cache/foes.json - so the
# numbers sit close to that player's level when we met them, and an hourly run only
# pays for new opponents. Paced under the 10/min limit and capped per run so a
# backlog never pushes a GitHub Actions job past its timeout; the rest waits for the
# next run (the early exit above knows to keep going while any are pending).
FOE_CACHE = ROOT / "cache" / "foes.json"
FOE_MAX = int(os.environ.get("FOE_MAX") or 40)


def foe_row(name, st):
    st = st or {}
    r = int(st.get("roundsPlayed") or 0)
    k, a, de = int(st.get("kills") or 0), int(st.get("assists") or 0), int(st.get("deaths") or 0)
    cur = st.get("currentTier") or {}
    return {"name": name, "t": cur.get("tier"), "s": cur.get("subTier"),
            "rp": int(st.get("currentRankPoint") or 0), "n": r,
            # the API's own "kda" field comes back 0 for ranked, so compute it
            "kda": round((k + a) / max(de, 1), 2) if r else None,
            "dmg": int(round(float(st.get("damageDealt") or 0) / r)) if r else None,
            "at": datetime.now(KST).strftime("%Y-%m-%d")}


try:
    foe_cache = json.loads(FOE_CACHE.read_text(encoding="utf-8"))
except Exception:
    foe_cache = {}
known = foe_cache.get(cur_season) or {}
need = []
for m in matches:                                   # newest match first
    for f in m.get("foes", []):
        if f["id"] not in known and f["id"] not in need:
            need.append(f["id"])
names = {f["id"]: f["name"] for m in matches for f in m.get("foes", [])}
looked = 0
if need:
    print("상대 %d명 조회 (이번 실행 최대 %d명, 6초 간격)" % (len(need), FOE_MAX), file=sys.stderr)
for aid in need[:FOE_MAX]:
    try:
        d = json.loads(fetch(BASE + "/players/%s/seasons/%s/ranked" % (aid, cur_season), H))
        st = (d["data"]["attributes"].get("rankedGameModeStats") or {}).get(MODE) or {}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print("  상대 조회 중단 (HTTP %s) - 다음 실행에서 이어 합니다" % e.code, file=sys.stderr)
            break
        st = {}
    except Exception as e:
        print("  상대 조회 중단 (%s) - 다음 실행에서 이어 합니다" % e, file=sys.stderr)
        break
    known[aid] = foe_row(names.get(aid, "?"), st)
    looked += 1
    if looked % 10 == 0:
        print("  %d/%d" % (looked, min(len(need), FOE_MAX)), file=sys.stderr)
    time.sleep(6.1)
foes_pending = len(need) - looked
FOE_CACHE.parent.mkdir(parents=True, exist_ok=True)
# only the current season is worth keeping
FOE_CACHE.write_text(json.dumps({cur_season: known}, ensure_ascii=False, indent=0, sort_keys=True),
                     encoding="utf-8")

seed = {"fetchedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "shard": SHARD, "mode": MODE, "matchType": MATCH_TYPE,
        "seasonId": cur_season, "seasonStart": season_start, "squad": SQUAD, "telemetry": True,
        "unionSig": union_sig, "highlights": hl_clips,
        "players": [{"name": f["name"], "accountId": f["accountId"],
                     "ranked": norm_ranked(ranked.get(f["accountId"], {}))} for f in found],
        "weapons": {nm: [[weap_label(k), v] for k, v in c.most_common(8)]
                    for nm, c in season_weapons.items()},
        "matches": matches,
        "foes": {aid: known[aid] for aid in names if aid in known},
        "foesPending": foes_pending}
if not matches:
    sys.exit("이번 시즌 경쟁전 스쿼드 경기를 찾지 못했습니다. 기존 페이지를 그대로 둡니다.")

blob = json.dumps(seed, ensure_ascii=False, separators=(",", ":"))
OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
OUT_JSON.write_text(blob, encoding="utf-8")

tpl = TEMPLATE.read_text(encoding="utf-8")
if "__SEED_JSON__" not in tpl:
    sys.exit("template.html 에 __SEED_JSON__ 자리표시자가 없습니다.")
OUT_HTML.write_text(tpl.replace("__SEED_JSON__", blob), encoding="utf-8")
print("wrote %s (%.0f KB)" % (OUT_HTML, OUT_HTML.stat().st_size / 1024), file=sys.stderr)

print("\nkept %d squad matches in %.0fs" % (len(matches), time.time() - t0), file=sys.stderr)
print("범위: %s ~ %s" % (matches[-1]["at"][:10], matches[0]["at"][:10]), file=sys.stderr)
games = collections.Counter()
for m in matches:
    for nm in m["members"]:
        games[nm] += 1
print("\n%-14s %8s %10s %8s" % ("member", "시즌판", "수집경기", "커버리지"), file=sys.stderr)
for p in seed["players"]:
    r = p["ranked"]["rounds"]
    g = games[p["name"]]
    print("%-14s %6d판 %8d판 %7s" % (p["name"], r, g,
          ("%.0f%%" % (g / r * 100)) if r else "-"), file=sys.stderr)
