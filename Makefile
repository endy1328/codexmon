PYTHON ?= python3
PYTHONPATH_VALUE ?= src

.PHONY: help run doctor daemon-serve monitor-serve test check lint fmt

help:
	@echo "make run      - codexmon CLI 도움말"
	@echo "make doctor   - 개발 기준선 점검 출력"
	@echo "make daemon-serve - background daemon worker 실행"
	@echo "make monitor-serve - live progress monitor HTTP server 실행"
	@echo "make test     - unittest 실행"
	@echo "make check    - Python compile check"
	@echo "make lint     - ruff check (설치된 경우)"
	@echo "make fmt      - ruff format (설치된 경우)"

run:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m codexmon --help

doctor:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m codexmon doctor

daemon-serve:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m codexmon daemon serve

monitor-serve:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m codexmon monitor serve

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m compileall src tests

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check src tests; \
	else \
		echo "ruff not installed"; \
	fi

fmt:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff format src tests; \
	else \
		echo "ruff not installed"; \
	fi
