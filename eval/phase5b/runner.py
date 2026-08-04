"""Narrow evaluator-to-worker entry point for the mock-only Phase 5B dry run."""

from __future__ import annotations

from ecomsre.phase5b.worker import MockWorkerResult, run_mock_worker


def worker_request(request: dict[str, object]) -> MockWorkerResult:
    return run_mock_worker(request)
