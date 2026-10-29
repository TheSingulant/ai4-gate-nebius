.PHONY: test demo live-e2e serve

PYTHON ?= python3
export PYTHONPATH := src:$(PYTHONPATH)

test:
	$(PYTHON) -m pytest -v

demo:
	$(PYTHON) -m gate_demo.demo --out demo-output

# Live Nemotron run. Requires NEBIUS_API_KEY in the environment.
# Writes redacted evidence to evidence/live-e2e.json. Does not print the key.
live-e2e:
	$(PYTHON) -m gate_demo.live_e2e --out evidence/live-e2e.json

serve:
	$(PYTHON) -m gate_demo.server --host 127.0.0.1 --port 8000
