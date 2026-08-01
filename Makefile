.PHONY: test check

test:
	python3 -m unittest discover -s tests -v

check: test
	bash -n config/coexist.sh
	sh -n bin/codex-provider
	./scripts/check-secrets.sh
