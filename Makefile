.PHONY: certs up down smoke pressure pressure-matrix passport-deep oberon test gate secret-scan release-audit clean

COMPOSE=podman compose -f deploy/compose/docker-compose.yml

certs:
	python tools/gates/generate_dev_certs.py

up: certs
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

smoke:
	python tools/smoke/foundation_smoke.py

pressure:
	python tools/smoke/foundation_pressure.py

pressure-matrix:
	python tools/smoke/foundation_pressure_matrix.py

passport-deep:
	python tools/smoke/passport_deep_test.py

oberon:
	bash tools/smoke/oberon_deploy_test.sh

oberon-full:
	bash tools/smoke/oberon_deploy_test.sh --profile full

test:
	cd tools/testing/policyfixtures && go test ./...
	cd s0/agent-registry-service && go test ./...
	cd s0/passport-issuance-engine && go test ./...
	cd sx/s0s1-rest-bff && go test ./...
	cd sx/are-api-gateway && go test ./...
	cd s0/immutable-ledger && cargo test
	cd s1/scope-evaluator-runtime && cargo test
	cd s1/opa-integration-layer && npm ci && npm test -- --runInBand

secret-scan:
	python tools/security/secret_scan.py

release-audit:
	python tools/security/release_audit.py

gate:
	python tools/gates/foundation_gate.py

clean:
	$(COMPOSE) down -v
	rm -rf reports deploy/compose/tls
