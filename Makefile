PYTHON ?= python
.PHONY: all init test run status
all: test
init:
	$(PYTHON) main.py init
test:
	$(PYTHON) -m unittest discover -s tests -v
run:
	$(PYTHON) main.py schedule --config config/discovery.json
	$(PYTHON) main.py work --max-jobs 20
status:
	$(PYTHON) main.py status
