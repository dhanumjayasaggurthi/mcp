.PHONY: test run production migrate export-worker indexing-worker docker-up docker-down

test:
	PYTHONPATH=. pytest -q tests
	python -m compileall -q enterprise_data_platform

run:
	EDP_REFERENCE_MODE=true uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080 --reload

production:
	uvicorn enterprise_data_platform.production_app:create_production_app --factory --host 0.0.0.0 --port 8080 --limit-concurrency 64 --timeout-graceful-shutdown 35 --no-access-log

migrate:
	python -m enterprise_data_platform.workers migrate

export-worker:
	python -m enterprise_data_platform.workers export

indexing-worker:
	python -m enterprise_data_platform.workers indexing

docker-up:
	docker compose up --build

docker-down:
	docker compose down
