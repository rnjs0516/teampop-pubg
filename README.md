# TEAMPOP PUBG Analytics

카카오 PUBG 고정 스쿼드 4인의 **경쟁전 시즌 전적** 대시보드. 로컬에서 빌드해 GitHub Pages 로 올립니다.

페이지는 정적 HTML 하나이고, 전적 데이터는 빌드 시점에 안에 박힙니다. 브라우저는 PUBG API 를 호출하지 않으므로 **API 키가 페이지에 들어가지 않습니다.**

## 설정

```powershell
cd teampop-pubg
Copy-Item .env.example .env
notepad .env          # PUBG_API_KEY 입력 (developer.pubg.com 발급)
python build.py       # docs/index.html 생성
```

`.env` 는 `.gitignore` 에 있어 커밋되지 않습니다.

## 원할 때 갱신하는 방법

**① 폰에서 — PC 불필요**

저장소 **Actions** 탭 → `전적 갱신` → **Run workflow** 버튼. GitHub 서버가 빌드하므로 PC 가 꺼져 있어도 됩니다.

처음 한 번만 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 `PUBG_API_KEY` 를 등록하세요. 이 값은 암호화되어 저장되고 저장소 코드나 로그에 노출되지 않습니다.

**② PC 에서**

바탕화면의 `TEAMPOP 전적 갱신` 바로가기를 더블클릭하거나:

```powershell
.\update.bat
```

빌드 → 변경 있으면 커밋 → 푸시까지 합니다. 변경이 없으면 푸시하지 않습니다.

## 자동 갱신

GitHub Actions 가 **매시간** 자동 실행됩니다 (`.github/workflows/update.yml`, 매 정각 5분). GitHub 서버에서 도므로 PC 와 무관합니다.

새 경기가 없으면 `/players` **1회만** 호출하고 즉시 끝납니다 — 멤버들의 경기 ID 목록 해시(`unionSig`)를 이전 결과와 비교해 변화가 없으면 빌드·커밋·푸시를 모두 건너뜁니다. 그래서 매시간 돌려도 비용이 거의 없습니다.

새 경기가 있을 때만 전체 분석(텔레메트리 포함, 약 40초)을 돌리고 푸시합니다. 즉 **게임을 하면 1시간 안에 알아서 반영**됩니다.

수동 실행 시 `force` 를 켜면 새 경기가 없어도 강제로 다시 빌드합니다.

아래 윈도우 작업 스케줄러는 Actions 를 쓰면 불필요합니다 — 지우려면:

```powershell
Unregister-ScheduledTask -TaskName "TEAMPOP PUBG 전적 갱신" -Confirm:$false
```

### (선택) 윈도우 작업 스케줄러

매일 오전 9시에 돌리는 예시:

```powershell
schtasks /create /tn "TEAMPOP PUBG 전적 갱신" /tr "%CD%\update.bat" /sc daily /st 09:00
```

확인·삭제:

```powershell
schtasks /query /tn "TEAMPOP PUBG 전적 갱신"
schtasks /run   /tn "TEAMPOP PUBG 전적 갱신"
schtasks /delete /tn "TEAMPOP PUBG 전적 갱신" /f
```

PC 가 켜져 있어야 실행됩니다.

## 멤버 변경

`build.py` 상단의 `SQUAD` 를 고치고 다시 빌드하면 됩니다.

```python
SQUAD = ["gunerz_", "DDalGi_GOD", "nM_-ShinyaT", "Moontari_"]
```

## 시즌이 바뀌면

PUBG API 는 시즌 시작일을 제공하지 않습니다 (`/seasons` 에는 `isCurrentSeason` 뿐이고, `match.seasonState` 는 시즌 교체 전후 모두 `progress` 입니다). 그래서 `template.html` 의 `SEASON_STARTS` 에 공식 패치 노트 기준 날짜를 적어 둡니다.

```js
const SEASON_STARTS = {
  "division.bro.official.pc-2018-44": "2026-11-04T08:30:00Z"
};
```

새 시즌 줄을 추가하지 않아도 동작합니다 — 그 경우 멤버별 시즌 판수에서 경계를 역산합니다. 적어두면 더 정확합니다.

## 집계 기준

- **경쟁전(`competitive`) 스쿼드**만. 일반전·아케이드·훈련장은 제외.
- 스쿼드 멤버가 **2명 이상 같은 팀**인 경기만.
- 멤버 전적표(티어·K/D·평균 등수 등)는 경쟁전 시즌 API 값이라 **시즌 전체** 기준.
- 어워드·POM·등수 추이는 위 조건으로 모은 **경기 데이터** 기준.
- PUBG API 의 경기 목록은 **약 14일**까지만 돌려줍니다. 시즌이 2주를 넘기면 초반 경기는 가져올 수 없습니다.

## API 호출량

갱신 1회에 레이트리밋 대상 호출 **6회** (`/players` 1 + `/seasons` 1 + 멤버별 `/ranked` 4). 개발용 키 제한은 분당 10회입니다.

`/matches` 와 텔레메트리 CDN 은 레이트리밋 대상이 아니며, 경기당 약 1.5MB 를 받아 분석합니다 (42경기 기준 약 63MB, 1분 내외).
