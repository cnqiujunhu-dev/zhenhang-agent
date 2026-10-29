from __future__ import annotations

import argparse
import json
import tempfile
import time
from importlib.resources import files
from pathlib import Path

from fastapi.testclient import TestClient

from .app import create_app
from .model_gateway import ModelGateway, RuleIntentModel


def run_eval(provider: str) -> dict:
    cases = json.loads(files("zhenxing").joinpath("eval_cases.json").read_text(encoding="utf-8"))
    gateway = ModelGateway(RuleIntentModel()) if provider == "rule" else ModelGateway.from_environment()
    results = []
    with tempfile.TemporaryDirectory(prefix="zhenxing-eval-") as directory:
        app = create_app(f"sqlite:///{(Path(directory) / 'eval.db').as_posix()}", gateway)
        with TestClient(app) as http:
            for case in cases:
                token = http.post("/api/v1/demo/sessions").json()["token"]
                headers = {"Authorization": f"Bearer {token}"}
                started = time.perf_counter()
                response = http.post("/api/v1/tasks", headers=headers,
                                     json={"message": case["message"]})
                initial = response.json() if response.status_code == 200 else {}
                initial_state_ok = ("initial_state" not in case or
                                    initial.get("state") == case["initial_state"])
                clarification_ok = True
                if response.status_code == 200 and "clarification" in case:
                    response = http.post(
                        f"/api/v1/tasks/{initial['task_id']}/clarify", headers=headers,
                        json={"message": case["clarification"],
                              "expected_task_version": initial["version"]},
                    )
                    clarification_ok = response.status_code == 200
                duration_ms = round((time.perf_counter() - started) * 1000)
                task = response.json() if response.status_code == 200 else {}
                node_keys = [item["node_id"] for item in task.get("plan", {}).get("nodes", [])]
                checks = {
                    "http_ok": response.status_code == 200,
                    "intent": task.get("intent_type") == case["expected_intent"],
                    "state": task.get("state") == case["expected_state"],
                    "initial_state": initial_state_ok,
                    "clarification_http_ok": clarification_ok,
                    "confirmation_boundary": (
                        not case.get("must_not_confirm", False) or task.get("confirmation_id") is None
                    ),
                    "required_nodes": all(node in node_keys for node in case.get("required_nodes", [])),
                }
                results.append({
                    "id": case["id"],
                    "pass": all(checks.values()),
                    "checks": checks,
                    "observed_intent": task.get("intent_type"),
                    "observed_state": task.get("state"),
                    "model_label": task.get("model_label"),
                    "duration_ms": duration_ms,
                })
        app.state.session_factory.kw["bind"].dispose()
    return {
        "provider": provider,
        "total": len(results),
        "passed": sum(item["pass"] for item in results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic banking-agent scenario evaluations")
    parser.add_argument("--provider", choices=["rule", "ollama"], default="rule")
    args = parser.parse_args()
    report = run_eval(args.provider)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
