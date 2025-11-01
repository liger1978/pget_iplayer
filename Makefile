UV := uv
APP_NAME := pget_iplayer
OUTPUT_DIR := build
OUTPUT_FILE := $(APP_NAME)
DOCKER_IMAGE := $(APP_NAME):latest
DOCKERFILE := Dockerfile
DOCKER_USER := $(shell id -u):$(shell id -g)
DOCKER_RUN_FLAGS := -v $(CURDIR):/workspace -w /workspace -u $(DOCKER_USER) \
	-e HOME=/workspace --rm
ifeq ($(OS),Windows_NT)
NUITKA_STATIC_LIBPYTHON :=
else
NUITKA_STATIC_LIBPYTHON := --static-libpython=yes
endif

NUITKA_FLAGS := --assume-yes-for-downloads --remove-output --onefile \
	--python-flag=-m --output-dir=$(OUTPUT_DIR) \
	--output-filename=$(OUTPUT_FILE) $(NUITKA_STATIC_LIBPYTHON) $(APP_NAME)

.PHONY: clean distclean build docker-image docker-build install-git-hooks lint lint-all test check check-all

clean:
	rm -rf $(OUTPUT_DIR) || true

distclean: clean
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + || true

build:
	$(UV) sync --group build
	$(UV) run nuitka $(NUITKA_FLAGS)

docker-image:
	docker build -t $(DOCKER_IMAGE) -f $(DOCKERFILE) .

docker-build: docker-image
	docker run $(DOCKER_RUN_FLAGS) $(DOCKER_IMAGE) bash -c '\
		$(UV) sync --group build && \
		$(UV) run nuitka $(NUITKA_FLAGS)'

install-git-hooks:
	$(UV) sync --group check
	$(UV) run pre-commit install

lint:
	$(UV) sync --group check
	$(UV) run pre-commit run

lint-all:
	$(UV) sync --group check
	$(UV) run pre-commit run --all-files

test:
	$(UV) sync --group check
	$(UV) run pytest -vv

check: lint test

check-all: lint-all test
