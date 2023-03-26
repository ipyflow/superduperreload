# -*- coding: utf-8 -*-
.PHONY: clean black blackcheck imports build deploy_only deploy check check_no_typing test tests deps devdeps dev typecheck version bump

clean:
	rm -rf __pycache__ core/__pycache__ build/ core/build/ core/dist/ dist/ superduperreload.egg-info/ core/superduperreload_core.egg-info

build: clean
	./scripts/build.sh

version:
	./scripts/build-version.py

bump:
	./scripts/bump.sh

deploy_only:
	./scripts/deploy.sh

deploy: version build deploy_only

black:
	isort ./core
	./scripts/blacken.sh

blackcheck:
	isort ./core --check-only
	./scripts/blacken.sh --check

imports:
	pycln ./core
	isort ./core

typecheck:
	./scripts/typecheck.sh

# this is the one used for CI, since sometimes we want to skip typcheck
check_no_typing:
	./scripts/runtests.sh

coverage:
	rm -f .coverage
	rm -rf htmlcov
	./scripts/runtests.sh --coverage
	mv core/.coverage .
	coverage html
	coverage report

xmlcov: coverage
	coverage xml

check: eslint blackcheck typecheck check_no_typing

test: check
tests: check

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -e .[dev]

dev: devdeps build
