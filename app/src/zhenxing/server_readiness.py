"""Restricted-server preflight. Never initializes schema, seeds data or logs secrets.

Only the process environment is inspected; no .env/private file is loaded. The
database probe is opt-in, PostgreSQL-only and rolls back a READ ONLY transaction.
Passing this narrow check is not an authorization to deploy or expose a service.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


MANUAL_GATES = (
    "VERIFY_TRUSTED_TLS_OR_RESTRICTED_SECURE_TUNNEL",
    "VERIFY_ADMIN_ACCESS_ISOLATION_AND_ATTACHMENT_PERMISSIONS",
    "REHEARSE_DATABASE_AND_ATTACHMENT_RESTORE_ON_SEPARATE_TARGET",
    "VERIFY_IMAGE_SCHEMA_ROLLBACK_COMPATIBILITY_AND_UNKNOWN_RECONCILIATION",
)


def _usable_secret(value: str, *, minimum: int) -> bool:
    clean = value.strip()
    lower = clean.lower()
    placeholders = ("change-me", "changeme", "replace", "placeholder", "your-", "example", "<", ">", "${")
    return (len(clean) >= minimum and len(set(clean)) >= 8
            and lower not in {"zhenxing", "password", "password123", "admin88"}
            and not any(marker in lower for marker in placeholders))


def configuration_issues(environment: Mapping[str, str]) -> list[str]:
    if environment.get("ZX_SERVER_PROFILE") != "restricted":
        return ["SERVER_PROFILE_NOT_CONFIGURED"]
    issues = []
    required = {
        "ZX_BOOTSTRAP_NAMED_ACCOUNTS": ("0", "DEFAULT_ACCOUNT_BOOTSTRAP_ENABLED"),
        "ZX_ALLOW_PUBLIC_REGISTRATION": ("0", "PUBLIC_REGISTRATION_NOT_DISABLED"),
        "ZX_ENABLE_LEGACY_COMMANDS": ("0", "LEGACY_COMMANDS_NOT_DISABLED"),
        "ZX_MODEL_PROVIDER": ("router", "CLOUD_PROVIDER_NOT_RESTRICTED"),
        "ZX_ROUTING_MODE": ("CLOUD_ONLY", "CLOUD_ONLY_POLICY_NOT_CONFIGURED"),
        "ZX_LAYA_MODE": ("OFF", "LAYA_MIGRATION_MODE_NOT_OFF"),
    }
    for key, (expected, code) in required.items():
        if environment.get(key) != expected:
            issues.append(code)
    if not _usable_secret(environment.get("ZX_DEEPSEEK_API_KEY", ""), minimum=20):
        issues.append("CLOUD_SECRET_MISSING_OR_PLACEHOLDER")
    if environment.get("ZX_DEEPSEEK_MODEL") not in {"deepseek-flash", "deepseek-v4-pro"}:
        issues.append("SUPPORTED_CLOUD_MODEL_NOT_CONFIGURED")
    try:
        url = make_url(environment.get("ZX_DATABASE_URL", ""))
        if (url.drivername != "postgresql+psycopg" or not url.host or not url.database
                or not url.username or not url.password or url.port != 5432):
            issues.append("POSTGRES_DATABASE_NOT_CONFIGURED")
        elif not _usable_secret(url.password, minimum=24):
            issues.append("DATABASE_SECRET_MISSING_OR_WEAK")
        if url.query:
            # Do not let URL-level connection options weaken the read-only probe.
            issues.append("DATABASE_URL_OPTIONS_REQUIRE_REVIEW")
    except Exception:
        # SQLAlchemy parser errors can contain a DSN; never return their text.
        issues.append("POSTGRES_DATABASE_NOT_CONFIGURED")
    if environment.get("ZX_ATTACHMENTS_DIR") != "/app/var/attachments":
        issues.append("PRIVATE_ATTACHMENT_PATH_NOT_CONFIGURED")
    return issues


def _matches_initial_password(username: str, encoded: str) -> bool:
    """Compare in memory only; neither the username nor digest leaves the probe."""
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100000 <= int(rounds) <= 1000000:
            return False
        calculated = hashlib.pbkdf2_hmac("sha256", (username + "88").encode(),
                                        bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(calculated, digest)
    except (ValueError, TypeError, AttributeError):
        return False


def _valid_hash(encoded: Any) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        return (algorithm == "pbkdf2_sha256" and 100000 <= int(rounds) <= 1000000
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (ValueError, TypeError, AttributeError):
        return False


def inspect_account_policy_rows(rows: list[Mapping[str, Any]], policy: Any) -> tuple[list[str], dict[str, int]]:
    """Aggregate without exposing identities, account IDs or credential hashes."""
    counts = {"accounts_scanned": len(rows), "initial_passwords_not_rotated": 0,
              "invalid_credential_hashes": 0, "active_admins": 0, "active_dev_test_accounts": 0}
    for row in rows:
        if not _valid_hash(row.get("password_hash")):
            counts["invalid_credential_hashes"] += 1
        elif _matches_initial_password(row.get("username", ""), row["password_hash"]):
            counts["initial_passwords_not_rotated"] += 1
        if row.get("status") == "ACTIVE" and row.get("role") == "ADMIN":
            counts["active_admins"] += 1
        if row.get("status") == "ACTIVE" and row.get("role") == "DEV_TEST":
            counts["active_dev_test_accounts"] += 1
    issues = []
    if counts["initial_passwords_not_rotated"]:
        issues.append("INITIAL_PASSWORD_ROTATION_REQUIRED")
    if counts["invalid_credential_hashes"]:
        issues.append("CREDENTIAL_HASH_REVIEW_REQUIRED")
    if not counts["active_admins"]:
        issues.append("ACTIVE_ADMIN_NOT_PROVISIONED")
    if counts["active_dev_test_accounts"]:
        issues.append("ACTIVE_DEV_TEST_REQUIRES_SEPARATE_ENVIRONMENT")
    if (not isinstance(policy, dict) or policy.get("mode") != "CLOUD_ONLY"
            or policy.get("cloud_enabled") is not True or policy.get("cloud_provider") != "deepseek"
            or policy.get("cloud_model") not in {"deepseek-flash", "deepseek-v4-pro"}):
        issues.append("PERSISTED_CLOUD_ONLY_POLICY_NOT_READY")
    return issues, counts


def probe_postgres(database_url: str, *, engine_factory: Callable = create_engine) -> tuple[list[str], dict[str, int]]:
    """Use raw SELECTs, no application/schema/bootstrap/login or bank routines."""
    engine = None
    try:
        engine = engine_factory(database_url, echo=False, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c default_transaction_read_only=on -c statement_timeout=5000"})
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                    return ["DATABASE_READONLY_NOT_ENFORCED"], {}
                connection.execute(text("SELECT 1")).scalar_one()
                rows = list(connection.execute(text(
                    "SELECT username, password_hash, role, status FROM login_accounts")).mappings())
                policy = connection.execute(text(
                    "SELECT policy_json FROM model_routing_policies WHERE id = 'global'")).scalar_one_or_none()
                return inspect_account_policy_rows(rows, policy)
            finally:
                transaction.rollback()
    except Exception:
        # Driver/connection messages can echo credentials, usernames and hosts.
        return ["DATABASE_READONLY_CHECK_FAILED"], {}
    finally:
        if engine is not None:
            engine.dispose()


def readiness_report(environment: Mapping[str, str], *, database_readonly: bool = False,
                     database_probe: Callable = probe_postgres) -> dict[str, Any]:
    issues = configuration_issues(environment)
    report: dict[str, Any] = {
        "schema_version": "server-readiness-v1",
        "status": "NOT_CONFIGURED" if issues == ["SERVER_PROFILE_NOT_CONFIGURED"] else "BLOCKED_CONFIG",
        "scope": "RESTRICTED_SERVER_PRECHECK_NOT_DEPLOYMENT_APPROVAL",
        "issue_codes": issues, "database_checked": False, "aggregate_counts": {},
        "manual_gates": {"verified_by_this_checker": False, "required": list(MANUAL_GATES)},
        "bank_operations": 0, "model_api_calls": 0, "mutation_attempts": 0,
        "credentials_or_identity_details_emitted": False,
    }
    if issues:
        return report
    if not database_readonly:
        report.update(status="DATABASE_NOT_CHECKED", issue_codes=["EXPLICIT_READONLY_DATABASE_PROBE_REQUIRED"])
        return report
    db_issues, counts = database_probe(environment["ZX_DATABASE_URL"])
    report.update(database_checked=True, issue_codes=db_issues, aggregate_counts=counts,
                  status="BLOCKED_DATABASE" if db_issues else "PRECHECK_PASSED_MANUAL_GATES_PENDING")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-readonly", action="store_true",
                        help="Explicitly read PostgreSQL readiness/credentials/policy in a read-only transaction")
    arguments = parser.parse_args(argv)
    report = readiness_report(os.environ, database_readonly=arguments.database_readonly)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # No status emitted here certifies TLS, backup restoration or public release.
    return 3 if report["status"] == "PRECHECK_PASSED_MANUAL_GATES_PENDING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
