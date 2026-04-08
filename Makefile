.PHONY: run test lint typecheck bundle clean

run:
	python -m video_annotator

test:
	pytest tests/

test-unit:
	pytest tests/unit/

test-integration:
	pytest tests/integration/

lint:
	ruff check video_annotator/ tests/
	ruff format --check video_annotator/ tests/

format:
	ruff format video_annotator/ tests/

typecheck:
	mypy video_annotator/

bundle:
	pyinstaller video_annotator.spec

clean:
	rm -rf build/ dist/ __pycache__ .pytest_cache .mypy_cache
	find . -name "*.pyc" -delete
