.PHONY: materialize test run docker-up docker-down

materialize:
	../../scripts/materialize_source.sh

test:
	PYTHONPATH=. pytest -q tests
	python -m compileall -q enterprise_data_platform

run:
	EDP_REFERENCE_MODE=true uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080 --reload

docker-up:
	docker compose up --build

docker-down:
	docker compose down
