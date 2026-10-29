"""Run the bounded journey and security checks in isolated databases."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    app = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="var/acceptance/2026-10-29/birthday-safety")
    args = parser.parse_args()
    output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=True)
    names = ["test_birthday_safety_journey.py", "test_life_plan_runtime.py",
        "test_runtime_security.py", "test_consultation_security.py"]
    command = [sys.executable, "-m", "pytest", *["tests/" + name for name in names],
        "-q", "-o", "junit_family=legacy", "--junitxml=" + str(output / "backend.xml")]
    result = subprocess.run(command, cwd=app, capture_output=True, text=True, encoding="utf8", errors="replace")
    (output / "console.txt").write_text(result.stdout + result.stderr, encoding="utf8")
    root = ET.parse(output / "backend.xml").getroot()
    cases = root.findall(".//testcase")
    journeys = [json.loads(p.attrib["value"]) for p in root.findall(".//property")
        if p.attrib.get("name") == "journey_evidence"]
    source_paths = ["src/zhenxing/life_execution_review.py", "src/zhenxing/life_plan_service.py",
        "src/zhenxing/life_plan_worker.py", "src/zhenxing/merchant_service.py",
        "web/src/LifePlanCards.tsx", "web/src/lifeExecutionReview.ts",
        *["tests/" + name for name in names], "scripts/build_birthday_safety_evidence.py"]
    evidence = {"schema_version": "birthday-safety-evidence-v1", "date": "2026-10-29",
        "scope": "ISOLATED_SQLITE_DOMAIN_AND_CONTROLLED_SECURITY_TESTS",
        "live_model_used": False, "external_bank_or_merchant_used": False,
        "time_advanced_by_test": True, "main_database_accessed": False,
        "test_count": len(cases), "failed": sum(c.find("failure") is not None or c.find("error") is not None for c in cases),
        "skipped": sum(c.find("skipped") is not None for c in cases), "journeys": journeys,
        "sources": {path: hashlib.sha256((app / path).read_bytes()).hexdigest() for path in source_paths},
        "junit_sha256": hashlib.sha256((output / "backend.xml").read_bytes()).hexdigest()}
    (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({k: evidence[k] for k in ["test_count", "failed", "skipped", "scope"]}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
