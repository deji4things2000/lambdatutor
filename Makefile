PYTHON ?= python3.10

.PHONY: repl test

repl:
	$(PYTHON) lambda_tutor.py

test:
	$(PYTHON) -m unittest discover -s tests -v
