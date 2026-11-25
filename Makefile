# ioncubefs: install, test, and manage the FUSE decoder.
#
# The system packages are Debian/Ubuntu names.  On another distribution,
# install the equivalents yourself and run `make install NO_APT=1`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV         ?= .venv
BACKEND_DIR  ?= third_party/ioncube-decompiler
BACKEND_REPO ?= https://github.com/xrobau/ioncube-decompiler.git
BACKEND_BRANCH ?= missing-opcodes
APT_PACKAGES ?= build-essential pkg-config git curl ca-certificates \
                python3 python3-dev python3-venv libfuse-dev fuse

IONCUBE_DIR ?= ioncube
IONCUBE_LOADERS_URL ?= https://downloads.ioncube.com/loader_downloads/ioncube_loaders_lin_x86-64.tar.gz
IONCUBE_ENCODER_URL ?= https://www.ioncube.com/eval_linux

# uv installs into ~/.local/bin; make sure recipes see it even when the
# installer ran earlier in this same make invocation.
export PATH := $(HOME)/.local/bin:$(PATH)
export UV_PROJECT_ENVIRONMENT := $(VENV)

# Every action target except help must run as root.  No sudo inside the
# recipes: if you are not root, run "sudo -i" or log in as root.
ROOT_CHECK = @[ "$$(id -u)" -eq 0 ] || { \
  echo "ioncubefs: must be run as root." >&2; \
  echo "Run 'sudo -i' or log in as root, then rerun this target." >&2; \
  exit 1; \
}

.PHONY: help install prereqs uv-setup backend venv test mount umount status update clean distclean ioncube

help:
	@printf '%s\n' \
	  'ioncubefs' \
	  '' \
	  '  make install        install system packages, uv, backend, and .venv' \
	  '  make venv           sync the Python environment with uv' \
	  '  make backend        clone the decoder backend if it is missing' \
	  '  make test           run the driver test suite' \
	  '  make ioncube        download loaders + eval encoder into ioncube/' \
	  '  make update         pull the backend and re-sync the environment' \
	  '  make mount SRC=DIR  mount DIR (optional DEST=MOUNTPOINT)' \
	  '  make umount         unmount all ioncubefs mounts (optional DEST=MOUNTPOINT)' \
	  '  make status         list active ioncubefs mounts' \
	  '  make clean          remove build/test caches' \
	  '  make distclean      also remove .venv' \
	  '' \
	  'Examples:' \
	  '  # as root: make install' \
	  '  make mount SRC=/srv/encoded OPTS="prewarm=4"' \
	  '  ./umount.sh                # or: make umount' \
	  '' \
	  'Options for make mount: OPTS="opt1 opt2" (ioncubefs -o options).' \
	  'See ./mount.sh --help for the scripts.'

install: prereqs uv-setup backend venv
	$(ROOT_CHECK)
	@chmod +x mount.sh umount.sh
	@printf '\nInstalled. Mount with:\n  ./mount.sh SOURCE [MOUNTPOINT] [OPTIONS...]\n'

prereqs:
	$(ROOT_CHECK)
	@if [ "$(NO_APT)" = "1" ]; then \
	  echo "Skipping system packages (NO_APT=1)."; \
	elif command -v apt-get >/dev/null 2>&1; then \
	  apt-get update && \
	  apt-get install -y $(APT_PACKAGES); \
	else \
	  echo "apt-get not found. Install these packages with your package manager," >&2; \
	  echo "then rerun: make install NO_APT=1" >&2; \
	  echo "  $(APT_PACKAGES)" >&2; \
	  exit 1; \
	fi
	@if [ ! -e /dev/fuse ]; then \
	  echo "warning: /dev/fuse is missing; load the fuse module (modprobe fuse)" >&2; \
	  echo "         or give the container --device /dev/fuse" >&2; \
	fi

uv-setup:
	$(ROOT_CHECK)
	@if command -v uv >/dev/null 2>&1; then \
	  uv --version; \
	elif command -v curl >/dev/null 2>&1; then \
	  echo "Installing uv..."; \
	  curl -LsSf https://astral.sh/uv/install.sh | sh; \
	else \
	  echo "curl is required to install uv" >&2; exit 1; \
	fi
	@command -v uv >/dev/null 2>&1 || { \
	  echo "uv was installed to $(HOME)/.local/bin; add it to PATH and rerun." >&2; \
	  exit 1; \
	}

backend:
	$(ROOT_CHECK)
	@if [ -d "$(BACKEND_DIR)/src/ioncube_re" ]; then \
	  echo "backend: $(BACKEND_DIR) is present"; \
	elif [ -d "$(BACKEND_DIR)/.git" ]; then \
	  echo "backend: $(BACKEND_DIR) exists but does not contain src/ioncube_re" >&2; \
	  exit 1; \
	else \
	  echo "Cloning $(BACKEND_REPO) ($(BACKEND_BRANCH))..."; \
	  git clone --branch "$(BACKEND_BRANCH)" "$(BACKEND_REPO)" "$(BACKEND_DIR)"; \
	fi

venv: backend uv-setup
	$(ROOT_CHECK)
	uv sync --extra test
	@echo "Python environment ready in $(VENV)/"

test: venv
	$(ROOT_CHECK)
	uv run pytest -q

update: backend uv-setup
	$(ROOT_CHECK)
	@if [ -d "$(BACKEND_DIR)/.git" ]; then \
	  git -C "$(BACKEND_DIR)" pull --ff-only || \
	    echo "backend not updated (local changes or no upstream); continuing." >&2; \
	fi
	uv sync --extra test

mount:
	$(ROOT_CHECK)
	@if [ -z "$(SRC)" ]; then \
	  echo "usage: make mount SRC=/path [DEST=/mountpoint] [OPTS='opt1 opt2']" >&2; \
	  exit 2; \
	fi
	@test -x "$(VENV)/bin/ioncubefs" || { \
	  echo "ioncubefs is not installed; run 'make install' first." >&2; exit 1; }
	@IONCUBEFS_BIN="$(VENV)/bin/ioncubefs" ./mount.sh "$(SRC)" $(if $(DEST),"$(DEST)") $(OPTS)

umount:
	$(ROOT_CHECK)
	@./umount.sh $(if $(DEST),"$(DEST)")

status:
	$(ROOT_CHECK)
	@./umount.sh --list

ioncube:
	$(ROOT_CHECK)
	@command -v curl >/dev/null 2>&1 || { \
	  echo "curl is required; run 'make install' first." >&2; exit 1; }
	@mkdir -p "$(IONCUBE_DIR)/downloads" "$(IONCUBE_DIR)/loaders" "$(IONCUBE_DIR)/encoder"
	@set -e; \
	loaders_tgz="$(IONCUBE_DIR)/downloads/ioncube_loaders_lin_x86-64.tar.gz"; \
	if [ -f "$(IONCUBE_DIR)/.loaders.done" ] && [ -n "$$(ls -A "$(IONCUBE_DIR)/loaders" 2>/dev/null)" ]; then \
	  echo "loaders: $(IONCUBE_DIR)/loaders (already present)"; \
	else \
	  if [ ! -s "$$loaders_tgz" ] || ! tar -tzf "$$loaders_tgz" >/dev/null 2>&1; then \
	    rm -f "$$loaders_tgz"; \
	    echo "Downloading $(IONCUBE_LOADERS_URL)"; \
	    curl -fL --retry 3 --connect-timeout 30 -o "$$loaders_tgz.part" "$(IONCUBE_LOADERS_URL)"; \
	    mv "$$loaders_tgz.part" "$$loaders_tgz"; \
	  fi; \
	  tar -tzf "$$loaders_tgz" >/dev/null; \
	  rm -rf "$(IONCUBE_DIR)/loaders"; \
	  mkdir -p "$(IONCUBE_DIR)/loaders"; \
	  tar -xzf "$$loaders_tgz" --no-same-owner -C "$(IONCUBE_DIR)/loaders" --strip-components=1; \
	  touch "$(IONCUBE_DIR)/.loaders.done"; \
	  echo "loaders: extracted to $(IONCUBE_DIR)/loaders"; \
	fi
	@set -e; \
	encoder_tgz="$(IONCUBE_DIR)/downloads/eval_encoder.tar.gz"; \
	if [ -f "$(IONCUBE_DIR)/.encoder.done" ] && [ -d "$(IONCUBE_DIR)/encoder/ioncube_encoder_evaluation" ]; then \
	  echo "encoder: $(IONCUBE_DIR)/encoder/ioncube_encoder_evaluation (already present)"; \
	else \
	  if [ ! -s "$$encoder_tgz" ] || ! tar -tzf "$$encoder_tgz" >/dev/null 2>&1; then \
	    rm -f "$$encoder_tgz"; \
	    echo "Downloading $(IONCUBE_ENCODER_URL)"; \
	    curl -fL --retry 3 --connect-timeout 30 -o "$$encoder_tgz.part" "$(IONCUBE_ENCODER_URL)"; \
	    mv "$$encoder_tgz.part" "$$encoder_tgz"; \
	  fi; \
	  tar -tzf "$$encoder_tgz" >/dev/null; \
	  rm -rf "$(IONCUBE_DIR)/encoder"; \
	  mkdir -p "$(IONCUBE_DIR)/encoder"; \
	  tar -xzf "$$encoder_tgz" --no-same-owner -C "$(IONCUBE_DIR)/encoder"; \
	  chmod +x "$(IONCUBE_DIR)"/encoder/ioncube_encoder_evaluation/bin/* 2>/dev/null || true; \
	  touch "$(IONCUBE_DIR)/.encoder.done"; \
	  echo "encoder: extracted to $(IONCUBE_DIR)/encoder"; \
	fi
	@echo "ionCube files are in $(IONCUBE_DIR)/"

clean:
	$(ROOT_CHECK)
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@rm -rf .pytest_cache build dist *.egg-info
	@echo "Cleaned build/test caches."

distclean: clean
	$(ROOT_CHECK)
	@rm -rf "$(VENV)"
	@echo "Removed $(VENV)."
