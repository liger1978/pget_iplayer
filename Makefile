UV ?= uv
UVX ?= uvx
APP_NAME ?= pget_iplayer
ENTRY_SCRIPT ?= pget_iplayer/__main__.py
PY_VERSION := $(shell cat .python-version)
SITE_PACKAGES := .venv/lib/python$(PY_VERSION)/site-packages
SITE_PACKAGES64 := .venv/lib64/python$(PY_VERSION)/site-packages
SPEC_DIR := .pyinstaller

.PHONY: build clean distclean

build:
	$(UV) sync
	$(UVX) pyinstaller --onefile --nowindow --clean --specpath $(SPEC_DIR) --name $(APP_NAME) --path $(SITE_PACKAGES) --path $(SITE_PACKAGES64) $(ENTRY_SCRIPT)

clean:
	rm -rf build dist $(SPEC_DIR)

distclean: clean
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + || true
