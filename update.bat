@echo off
rem TEAMPOP PUBG - 전적 갱신 후 GitHub Pages 로 푸시
rem 작업 스케줄러에 등록해두면 지정한 시각마다 자동 실행됩니다.
setlocal
cd /d "%~dp0"

set "PYTHON=C:\Users\2024961\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [%date% %time%] build start
"%PYTHON%" build.py
if errorlevel 1 (
  echo [%date% %time%] build FAILED - 기존 페이지를 유지하고 종료합니다
  exit /b 1
)

git add docs highlights.json
git diff --cached --quiet && (
  echo [%date% %time%] 변경 없음 - 푸시 생략
  exit /b 0
)

git commit -m "전적 갱신 %date% %time%"
if errorlevel 1 (
  echo [%date% %time%] commit FAILED
  exit /b 1
)

git push
if errorlevel 1 (
  echo [%date% %time%] push FAILED - 인증을 확인하세요
  exit /b 1
)

echo [%date% %time%] 완료
exit /b 0
