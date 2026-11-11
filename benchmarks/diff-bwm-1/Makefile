# diff-bwm-1 convenience targets. Override the interpreter with PYTHON=...
PYTHON ?= python3
DBWM := $(PYTHON) -m dbwm

.PHONY: help selftest test test-core hygiene image image-clean image-lock canary

help:
	@echo "make selftest     core checks + every eval's selftest (no LLM, no network)"
	@echo "make test         full pytest suite (container tests skip without Docker + image)"
	@echo "make test-core    core runtime/analysis tests only"
	@echo "make hygiene      release hygiene scan (paths, secrets, oversize files)"
	@echo "make canary       check private/<evalN>/CANARY.txt"
	@echo "make image        build the agent image dbwm-agent:1.0.0 (docker/Dockerfile)"
	@echo "make image-clean  remove leftover dbwm trial containers and networks"
	@echo "make image-lock   print a fresh pin set for docker/requirements.txt (from docker/requirements.in)"

selftest:
	$(DBWM) selftest

test:
	$(PYTHON) -m pytest tests

test-core:
	$(PYTHON) -m pytest tests/core

hygiene:
	$(DBWM) hygiene

canary:
	$(DBWM) canary

image:
	$(DBWM) image build

image-clean:
	$(DBWM) image clean

# Resolve docker/requirements.in inside the base image and print the full pin set;
# paste it under the header of docker/requirements.txt, then `make image`.
image-lock:
	docker run --rm -i python:3.12-slim sh -c \
	  "pip install -q --root-user-action=ignore -r /dev/stdin >/dev/null && pip check >&2 && pip freeze" \
	  < docker/requirements.in
