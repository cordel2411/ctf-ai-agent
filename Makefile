.PHONY: env up down bootstrap check run test
env:        ## create .env with random secrets and fetch CTFd source
	./scripts/init-env.sh && ./scripts/fetch-ctfd.sh
up: env     ## start CTFd (docker compose)
	docker compose up -d --build
down:
	docker compose down
bootstrap:  ## set up CTFd, accounts, tokens and challenges
	ctf-agent bootstrap
check:
	ctf-agent check
run:
	ctf-agent run
test:
	python -m pytest tests/test_unit.py -q
