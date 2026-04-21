# 작업 패킷 B1 검증

날짜: 2026-04-21

검증 범위: 저장소 초기화와 개발 기준선 구축

## 점검 항목

- Git repository가 `main` branch 기준으로 초기화됐다
- Python 3.11 기준 `src/` 기반 프로젝트 구조가 생성됐다
- `pyproject.toml`이 패키징 메타데이터와 개발 도구 기준선을 정의한다
- `.gitignore`와 `.env.example`이 로컬 개발 기본선을 제공한다
- 최소 CLI 엔트리포인트가 존재하고 `version`, `doctor` command를 제공한다
- `unittest` 기반 baseline test가 존재한다
- `Makefile`이 최소 실행, 테스트, 점검 명령을 제공한다

## 실행 검증

- `PYTHONPATH=src python3 -m codexmon version`
- `PYTHONPATH=src python3 -m codexmon doctor`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`

## 검증 결과

통과

## 메모

- `uv`, `pytest`, `ruff`는 현재 시스템에 설치되어 있지 않아 stdlib-first 기준선으로 시작했다
- `pyproject.toml`에는 이후 설치 가능한 개발 의존성 정의를 남겨 두었다
- Git 미초기화 상태는 계획대로 B1 안에서 해소됐다
