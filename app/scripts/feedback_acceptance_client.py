"""Reusable opt-in API acceptance client. Importing performs no network work.

No CLI, deployment, model invocation, database access, payment retry, synthetic
bank result, or production-code modification is provided by this module.
Real execution is the root runner's responsibility; tests use MockTransport.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json as json_module
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlencode
from uuid import uuid4

import httpx

try:
    from scripts.feedback_acceptance_validation import (AcceptanceStopped, exact_json, require, public_projection_matches,
        response_error_code, rows, validated_cleanup_read_completions, validated_confirmation_pair, validated_expected_constraints)
except ModuleNotFoundError:  # Also usable by an explicitly invoked scripts/*.py runner.
    from feedback_acceptance_validation import (AcceptanceStopped, exact_json, require, public_projection_matches,
        response_error_code, rows, validated_cleanup_read_completions, validated_confirmation_pair, validated_expected_constraints)


_SECRET_KEY = re.compile(r"password|token|secret|authorization|demo_code|^code$|api_key|reasoning|chain_of_thought|raw_prompt|raw_response|challenge_id", re.I)
_STOP_STATES = frozenset({"AWAITING_CONFIRMATION", "NEED_CLARIFICATION", "SUCCEEDED", "FAILED", "UNKNOWN",
    "PARTIAL", "CLOSED", "CANCELLED", "WAITING", "WAITING_TIME", "PENDING_REVIEW", "AWAITING_APPROVAL", "PAUSED"})
_FORBIDDEN_PATH = re.compile(r"/(?:simulate[^/]*|advance|fault|retry-understanding|payment-task)(?:/|$)", re.I)
_SAFE_USAGE_COUNTERS = frozenset({"prompt_tokens","completion_tokens","total_tokens","input_tokens","output_tokens",
    "cached_tokens","reasoning_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens","max_tokens"})
try:
    from scripts.feedback_semantic_diagnostic_codes import DIAGNOSTIC_CODES as _SAFE_SEMANTIC_VALIDATION_REASONS
except ModuleNotFoundError:
    from feedback_semantic_diagnostic_codes import DIAGNOSTIC_CODES as _SAFE_SEMANTIC_VALIDATION_REASONS
_SAFE_SEMANTIC_LOCATION_FIELDS = frozenset({"mode", "action_kinds", "dependencies", "focus_ref", "entity_refs", "changes",
    "knowledge_topics", "clarification", "confidence", "memory_updates", "retrieval_query", "presentation_focus", "read_targets",
    "read_subjects", "question_aspects", "scope_frame_refs", "operation_scope", "clarification_evidence", "query_reference",
    "query_base", "query_base_source", "rule_aspects", "preference_rebase_ref", "action_ref", "field", "operation", "source_span",
    "source_text", "entity_ref", "selected_options", "start", "end", "<unregistered-field>"})


def _hash(value):
    return hashlib.sha256(json_module.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False).encode()).hexdigest()


def safe_json(value, secrets=(), *, _path=()):
    if isinstance(value, dict):
        if len(_path) >= 3 and _path[-3:-1] == ("semantic_diagnostics", "semantic_validation_errors") and type(_path[-1]) is int:
            location = value.get("location", [])
            result = {"location": [part if type(part) is int and 0 <= part <= 24 or isinstance(part, str)
                and part in _SAFE_SEMANTIC_LOCATION_FIELDS else "<unregistered-field>" for part in location[:6]]
                if isinstance(location, list) else []}
            reason = value.get("code")
            if isinstance(reason, str) and reason in _SAFE_SEMANTIC_VALIDATION_REASONS:
                result["validation_reason"] = safe_json(reason, secrets)
            elif 'code' in value:
                result['unregistered_reason_count'] = 1
            return result
        return {str(key):safe_json(item, secrets, _path=(*_path, str(key))) for key,item in value.items() if not _SECRET_KEY.search(str(key))
            or key in _SAFE_USAGE_COUNTERS and type(item) is int and item >= 0
            or key == "challenge_id_hash" and isinstance(item,str) and re.fullmatch(r"[0-9a-f]{64}",item)}
    if isinstance(value, (list, tuple)):
        return [safe_json(item, secrets, _path=(*_path, index)) for index, item in enumerate(value)]
    if isinstance(value, str):
        for secret in sorted((item for item in secrets if isinstance(item, str) and item), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*|\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return value
    return value


def _record_failure(function):
    @wraps(function)
    def recorded(self, *args, **kwargs):
        try:
            return function(self,*args,**kwargs)
        except Exception as error:
            record = {"operation":function.__name__,"exception_type":type(error).__name__,
                "code":getattr(error,"code","ACCEPTANCE_CLIENT_FAILURE")}
            self.event("failure",**record)
            self.save_json("failure",record)
            raise
    return recorded


class AcceptanceClient:
    def __init__(self, base_url, actor, output_dir, *, phase, allowed_actions=(), allowed_direct_writes=(),
            transport=None, poll_interval=0.4, poll_timeout=120.0):
        parsed = urlsplit(base_url)
        require(parsed.scheme in {"http","https"} and parsed.netloc and not parsed.username and not parsed.password
            and parsed.path in {"","/"} and not parsed.query and not parsed.fragment, "API_BASE_URL_INVALID")
        require(actor in {"quyuwei","wangwei","qiujunhu","admin","recording","recording_family","recording_friend",
            "qa1024_main","qa1024_peer1","qa1024_peer2","qa1026_eval","qa1026_peer","qa1028_flow","qa1028_peer","qa1028_credit","qa1028_schedule"}, "ACCEPTANCE_ACTOR_NOT_AUTHORIZED")
        require(isinstance(phase,str) and bool(phase), "ACCEPTANCE_PHASE_REQUIRED")
        require(isinstance(poll_interval,(int,float)) and 0 <= poll_interval <= 10
            and isinstance(poll_timeout,(int,float)) and 0 < poll_timeout <= 3600, "POLL_LIMIT_INVALID")
        self.base_url, self.actor, self.phase = base_url.rstrip("/"), actor, phase
        self.allowed_actions = frozenset(allowed_actions)
        require(all(isinstance(action,str) and action for action in self.allowed_actions), "ACTION_ALLOWLIST_INVALID")
        if actor in {"wangwei","qiujunhu","recording_family","recording_friend"}:
            require(self.allowed_actions <= {"AA_PAY"}, "PEERS_AA_PAYMENT_ONLY")
        if actor == "admin":
            require(self.allowed_actions <= {"CREDIT_REVIEW"}, "ADMIN_APPROVAL_ONLY")
        self.allowed_direct_writes = frozenset((method.upper(), path) for method,path in allowed_direct_writes)
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self._progress = (self.output_dir / "progress.jsonl").open("x", encoding="utf-8")
        self._http = httpx.Client(base_url=self.base_url, transport=transport, timeout=30, follow_redirects=False, trust_env=False)
        self.poll_interval, self.poll_timeout = float(poll_interval), float(poll_timeout)
        self._token = None
        self._secrets = set()
        self._counter = 0
        self._created_tasks = set()
        self._submitted_turns = {}
        self._unresolved_threads = {}
        self._attempted_confirmations = set()
        self._attempted_cancel = set()
        self.last_http = None
        self.save_json("run", {"actor":actor,"phase":phase,"base_url":self.base_url,
            "allowed_actions":sorted(self.allowed_actions), "allowed_direct_writes":sorted(self.allowed_direct_writes),
            "automatic_model_retry":False, "snapshot_origin":"ACTUAL_ENDPOINT_RESPONSES_WITH_AUTH_SECRETS_REMOVED"})

    def __repr__(self):
        return f"AcceptanceClient(actor={self.actor!r}, phase={self.phase!r}, credentials=<memory only>)"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.event("failure", exception_type=exc_type.__name__, code=getattr(exc,"code","CALLER_FAILURE"))
        self.close()

    def close(self):
        self._token = None
        self._secrets.clear()
        self._http.close()
        if not self._progress.closed:
            self._progress.close()

    def save_json(self, label, value):
        require(isinstance(label,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", label), "ARTIFACT_LABEL_INVALID")
        self._counter += 1
        target = self.output_dir / f"{self._counter:06d}-{label}.json"
        with target.open("x", encoding="utf-8") as stream:
            json_module.dump(safe_json(value,self._secrets), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        return target

    def event(self, kind, **fields):
        require(isinstance(kind,str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",kind), "EVENT_KIND_INVALID")
        record = {"at":datetime.now(timezone.utc).isoformat(),"event":kind,"actor":self.actor,"phase":self.phase,
            **safe_json(fields,self._secrets)}
        self._progress.write(json_module.dumps(record,ensure_ascii=False,allow_nan=False)+"\n")
        self._progress.flush()

    @_record_failure
    def req(self, method, path, *, json=None, expected=(200,), write_action=None):
        return self._request(method,path,json=json,expected=expected,write_action=write_action,internal=False)

    def _request(self, method, path, *, json=None, expected=(200,), write_action=None, internal=False):
        method = method.upper()
        parsed = urlsplit(path)
        require(isinstance(path,str) and path.startswith("/api/") and not parsed.scheme and not parsed.netloc
            and not parsed.fragment and ".." not in parsed.path and not _FORBIDDEN_PATH.search(parsed.path), "API_PATH_NOT_ALLOWED")
        require(method in {"GET","POST","PATCH","PUT","DELETE"}, "HTTP_METHOD_NOT_ALLOWED")
        if self.actor == "admin" and parsed.path not in {"/api/v2/auth/login","/api/v2/auth/me","/api/v2/health/ready"}:
            require(parsed.path.startswith("/api/v2/admin/credit/applications"), "ADMIN_APPROVAL_ONLY")
        if method != "GET" and not internal:
            require((method,parsed.path) in self.allowed_direct_writes and write_action in self.allowed_actions,
                "DIRECT_WRITE_NOT_EXPLICITLY_ALLOWED")
            require(not re.search(r"/(?:confirm|cancel|challenges|verify)(?:/|$)", parsed.path), "CONTROLLED_METHOD_REQUIRED")
            if self.actor in {"wangwei","qiujunhu"}:
                raise AcceptanceStopped("PEERS_AA_PAYMENT_CONFIRM_ONLY")
            if self.actor == "admin":
                require(method == "POST" and re.fullmatch(r"/api/v2/admin/credit/applications/[A-Za-z0-9-]+/decision",parsed.path)
                    and write_action == "CREDIT_REVIEW", "ADMIN_APPROVAL_ONLY")
        request_id = uuid4().hex
        headers = {"X-Request-ID":request_id}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        started = time.perf_counter()
        status = None
        try:
            response = self._http.request(method,path,json=json,headers=headers)
            status = response.status_code
            supplied_id = response.headers.get("X-Request-ID")
            if supplied_id and re.fullmatch(r"[A-Za-z0-9._:-]{1,120}",supplied_id):
                request_id = supplied_id
            try:
                data = response.json()
            except ValueError:
                raise AcceptanceStopped("STRUCTURED_JSON_RESPONSE_REQUIRED") from None
            require(isinstance(data,(dict,list)), "STRUCTURED_JSON_RESPONSE_REQUIRED")
            if parsed.path.startswith("/api/v2/auth/") and isinstance(data,dict):
                for key in ("token","access_token","refresh_token","demo_code","code","challenge_id"):
                    if isinstance(data.get(key),str) and data[key]:
                        self._secrets.add(data[key])
            if status not in expected:
                self.event("api_failure",error_code=response_error_code(data),status=status)
                raise AcceptanceStopped("HTTP_STATUS_" + str(status) + "_NO_REISSUE")
            return data
        except httpx.HTTPError as error:
            self.event("failure",exception_type=type(error).__name__,code="HTTP_TRANSPORT_UNKNOWN_NO_REISSUE")
            raise AcceptanceStopped("HTTP_TRANSPORT_UNKNOWN_NO_REISSUE") from None
        except AcceptanceStopped as error:
            self.event("failure",exception_type=type(error).__name__,code=error.code)
            raise
        finally:
            self.last_http = {"method":method,"path":parsed.path,"status":status,
                "elapsed_ms":round((time.perf_counter()-started)*1000,3),"request_id":request_id}
            self.event("http",**self.last_http)

    @_record_failure
    def login(self, password=None):
        require(self._token is None, "ALREADY_LOGGED_IN")
        password = self.actor + "88" if password is None else password
        require(isinstance(password,str) and bool(password), "LOGIN_PASSWORD_REQUIRED")
        self._secrets.add(password)
        try:
            result = self._request("POST","/api/v2/auth/login",json={"username":self.actor,"password":password},internal=True)
        finally:
            password = None
        token = result.get("token")
        require(isinstance(token,str) and bool(token), "LOGIN_TOKEN_REQUIRED")
        self._token = token
        self._secrets.add(token)
        identity = self.req("GET","/api/v2/auth/me")
        require(identity.get("username") == self.actor and identity.get("role") == ("ADMIN" if self.actor == "admin" else "CUSTOMER"),
            "EXACT_ACTOR_IDENTITY_REQUIRED")
        safe_identity = {key:identity.get(key) for key in ("username","role","user_id","sandbox_id")}
        self.save_json("identity",safe_identity)
        return safe_identity

    @_record_failure
    def snapshots(self, task_id):
        require(isinstance(task_id,str) and re.fullmatch(r"[A-Za-z0-9-]{1,80}",task_id), "TASK_ID_INVALID")
        trace = self.req("GET",f"/api/v2/tasks/{task_id}/trace")
        customer = self.req("GET",f"/api/v3/customer/tasks/{task_id}")
        require(trace.get("task_id") == customer.get("task_id") == task_id, "TASK_SNAPSHOT_ID_MISMATCH")
        value = {"trace":trace,"customer":customer,"state":self._state(trace),"task_id":task_id,
            "origins":{"trace":"/api/v2/tasks/{task_id}/trace","customer":"/api/v3/customer/tasks/{task_id}"}}
        self.save_json("task-snapshots",value)
        return value

    @staticmethod
    def _state(trace):
        state = trace.get("state")
        if state in _STOP_STATES:
            return state
        statuses = {node.get("status") for node in trace.get("nodes",[]) if isinstance(node,dict)}
        if "UNKNOWN" in statuses:
            return "UNKNOWN"
        if "WAITING_TIME" in statuses:
            return "WAITING_TIME"
        return state

    @_record_failure
    def settle(self, task_id, *, timeout=None):
        end = time.monotonic() + (self.poll_timeout if timeout is None else timeout)
        last = None
        while True:
            last = self.snapshots(task_id)
            if last["state"] in _STOP_STATES:
                self.event("task_observed",task_id=task_id,state=last["state"],version=last["trace"].get("version"))
                return last
            if time.monotonic() >= end:
                self.save_json("task-timeout",last)
                raise AcceptanceStopped("TASK_OBSERVATION_TIMEOUT_NO_REISSUE",observation=last)
            time.sleep(self.poll_interval)

    @_record_failure
    def observe_turn(self, conversation_id, turn_id, *, timeout=None):
        end = time.monotonic() + (self.poll_timeout if timeout is None else timeout)
        publishing_task_id, publication_waits = None, 0
        while True:
            if publication_waits and time.monotonic() >= end:
                self.save_json("turn-timeout", value)
                raise AcceptanceStopped("TURN_OBSERVATION_TIMEOUT_NO_REISSUE", observation=value)
            raw = self.req("GET",f"/api/v3/conversations/{conversation_id}")
            public = self.req("GET",f"/api/v3/customer/conversations/{conversation_id}")
            raw_rows = [row for row in rows(raw,"turns") if row.get("turn_id") == turn_id]
            public_rows = [row for row in rows(public,"turns") if row.get("turn_id") == turn_id]
            require(len(raw_rows) == len(public_rows) == 1, "EXACT_ORIGINAL_TURN_REQUIRED")
            raw_turn, public_turn = raw_rows[0], public_rows[0]
            require(raw_turn.get("conversation_id") == public_turn.get("conversation_id") == conversation_id,
                "TURN_CONVERSATION_ID_MISMATCH")
            raw_link, public_link = raw_turn.get("task_link"), public_turn.get("task_link")
            links = [link for link in (raw_link, public_link) if link is not None]
            require(all(isinstance(link, dict) and isinstance(link.get("task_id"), str)
                and re.fullmatch(r"[A-Za-z0-9-]{1,80}", link["task_id"]) for link in links), "TURN_TASK_LINK_ID_MISMATCH")
            ids = {link["task_id"] for link in links}
            require(len(ids) <= 1 and (publishing_task_id is None or not ids or ids == {publishing_task_id}),
                "TURN_TASK_LINK_ID_MISMATCH")
            if ids:
                publishing_task_id = next(iter(ids))
            value = {"conversation_id":conversation_id,"turn_id":turn_id,"raw_turn":raw_turn,"public_turn":public_turn,
                "task_link":raw_turn.get("task_link"),"trace":None,"customer":None,"state":raw_turn.get("status"),
                "turn_status":raw_turn.get("status"),"model_diagnostics":raw_turn.get("diagnostics",{}),"origins":{"raw_turn":"/api/v3/conversations/{conversation_id}",
                    "public_turn":"/api/v3/customer/conversations/{conversation_id}"}}
            if (raw_link is None) != (public_link is None) or publishing_task_id is not None and not links:
                # Two sequential GETs may straddle publication of THIS turn's
                # task link. Re-read the same records within the original
                # budget; never resubmit the message or borrow a new task ID.
                publication_waits += 1
                self._unresolved_threads[conversation_id] = turn_id
                value.update(state="UNKNOWN", link_publication_pending=True, observed_task_id=publishing_task_id)
                if publication_waits == 1:
                    self.save_json("turn-link-publication-pending", value)
                    self.event("turn_link_publication_wait", conversation_id=conversation_id, turn_id=turn_id,
                        observed_task_id=publishing_task_id)
                if time.monotonic() >= end:
                    self.save_json("turn-timeout", value)
                    raise AcceptanceStopped("TURN_OBSERVATION_TIMEOUT_NO_REISSUE", observation=value)
                time.sleep(self.poll_interval)
                continue
            if raw_turn.get("status") in {"ANSWERED","FAILED","UNKNOWN","CLOSED","CANCELLED"}:
                self._unresolved_threads.pop(conversation_id,None)
                link = value["task_link"]
                if isinstance(link,dict) and isinstance(link.get("task_id"),str):
                    submitted = self._submitted_turns.get((conversation_id,turn_id))
                    if submitted and raw_turn.get("mode") == "NEW_TASK" and submitted.get("current_task_id") is None:
                        self._created_tasks.add(link["task_id"])
                    value.update(self.settle(link["task_id"],timeout=max(0,end-time.monotonic())))
                self.save_json("turn-observation",value)
                self.event("turn_observed",conversation_id=conversation_id,turn_id=turn_id,mode=raw_turn.get("mode"),state=value["state"])
                return value
            if time.monotonic() >= end:
                self._unresolved_threads[conversation_id] = turn_id
                self.save_json("turn-timeout",value)
                raise AcceptanceStopped("TURN_OBSERVATION_TIMEOUT_NO_REISSUE",observation=value)
            time.sleep(self.poll_interval)

    @_record_failure
    def turn(self, message, *, conversation_id=None, title=None, current_task_id=None, expected_task_version=None,
            context_policy="AUTO", timeout=None):
        require(self.actor != "admin", "ADMIN_APPROVAL_ONLY")
        require(self._token is not None, "LOGIN_REQUIRED")
        require(isinstance(message,str) and 1 <= len(message) <= 2000, "TURN_MESSAGE_INVALID")
        require(context_policy in {"AUTO","INDEPENDENT"}, "CONTEXT_POLICY_INVALID")
        require(not conversation_id or conversation_id not in self._unresolved_threads, "ORIGINAL_TURN_MUST_BE_OBSERVED_FIRST")
        client_request_id = uuid4().hex
        if conversation_id is None:
            created = self._request("POST","/api/v3/customer/conversations",json={"title":title or "Feedback acceptance " + self.phase,
                "client_request_id":uuid4().hex},expected=(201,),internal=True)
            conversation_id = created.get("conversation_id")
            require(isinstance(conversation_id,str) and bool(conversation_id), "CONVERSATION_ID_REQUIRED")
        payload = {"message":message,"client_request_id":client_request_id,"context_policy":context_policy}
        if current_task_id is not None:
            require(isinstance(current_task_id,str) and type(expected_task_version) is int and expected_task_version > 0,
                "EXPLICIT_TASK_VERSION_REQUIRED")
            payload.update(current_task_id=current_task_id,expected_task_version=expected_task_version)
        self.event("turn_submitting",conversation_id=conversation_id,client_request_id=client_request_id)
        try:
            accepted = self._request("POST",f"/api/v3/customer/conversations/{conversation_id}/turns",json=payload,expected=(202,),internal=True)
        except AcceptanceStopped as error:
            if error.code != "HTTP_TRANSPORT_UNKNOWN_NO_REISSUE":
                raise
            self._unresolved_threads[conversation_id] = client_request_id
            acknowledgement = self.req("GET","/api/v3/customer/conversations/acknowledgement?" + urlencode({
                "conversation_id":conversation_id,"client_request_id":client_request_id}),expected=(200,404))
            accepted = acknowledgement.get("turn") if isinstance(acknowledgement,dict) else None
            if not isinstance(accepted,dict) or not isinstance(accepted.get("turn_id"),str):
                unknown = {"conversation_id":conversation_id,"client_request_id":client_request_id,"turn_id":None,
                    "raw_turn":None,"public_turn":None,"task_link":None,"trace":None,"customer":None,"state":"UNKNOWN",
                    "automatic_reissue":False}
                self.save_json("turn-unknown",unknown)
                return unknown
        turn_id = accepted.get("turn_id")
        require(isinstance(turn_id,str) and bool(turn_id), "TURN_ID_REQUIRED")
        self.event("turn_accepted", conversation_id=conversation_id, turn_id=turn_id, client_request_id=client_request_id)
        self._submitted_turns[(conversation_id,turn_id)] = {"client_request_id":client_request_id,"current_task_id":current_task_id}
        self._unresolved_threads[conversation_id] = turn_id
        return self.observe_turn(conversation_id,turn_id,timeout=timeout)

    @_record_failure
    def confirm(self, observation, *, expected_action, expected_constraints, validate_scope=None, timeout=None):
        require(expected_action in self.allowed_actions and self.actor != "admin", "CONFIRM_ACTION_NOT_AUTHORIZED")
        if self.actor in {"wangwei","qiujunhu"}:
            require(expected_action == "AA_PAY", "PEERS_AA_PAYMENT_ONLY")
        require(isinstance(observation,dict) and isinstance(observation.get("customer"),dict)
            and isinstance(observation.get("trace"),dict), "SELECTED_CUSTOMER_OBSERVATION_REQUIRED")
        validated_confirmation_pair(observation["customer"],observation["trace"],expected_action)
        task_id = observation["customer"].get("task_id")
        prepared = self.snapshots(task_id)
        self.save_json("prepared-confirmation",{"selected":observation,"fresh":prepared})
        pending,binding = validated_confirmation_pair(prepared["customer"],prepared["trace"],expected_action,
            selected_customer=observation["customer"])
        node = next(item for item in prepared["trace"]["nodes"] if item["node_id"] == binding["node_id"])
        validated_expected_constraints(pending,node,prepared["trace"],prepared["customer"],expected_constraints,validate_scope)
        pair_key = (task_id,pending["confirmation_id"])
        require(pair_key not in self._attempted_confirmations, "CONFIRMATION_ALREADY_ATTEMPTED_OBSERVE_ORIGINAL")
        self._attempted_confirmations.add(pair_key)
        prepared_source_hash = _hash({"trace":prepared["trace"],"customer":prepared["customer"],"binding":binding})
        payload = {key:pending[key] for key in ("confirmation_id","plan_hash","expected_task_version")}
        step_started = time.perf_counter()
        step = {"required":pending["requires_step_up"],"verified":False}
        if pending["requires_step_up"]:
            challenge = self._request("POST","/api/v2/auth/challenges",json={"task_id":pending["task_id"],
                "confirmation_id":pending["confirmation_id"]},internal=True)
            code = challenge.get("demo_code")
            challenge_id = challenge.get("challenge_id")
            require(isinstance(code,str) and re.fullmatch(r"\d{6}",code) and isinstance(challenge_id,str) and bool(challenge_id),
                "IN_MEMORY_DEMO_CHALLENGE_REQUIRED")
            self._secrets.update((code,challenge_id))
            try:
                checked = self._request("POST",f"/api/v2/auth/challenges/{challenge_id}/verify",json={"code":code},internal=True)
            finally:
                code = None
            require(checked.get("verified") is True, "STEP_UP_NOT_VERIFIED")
            payload["auth_challenge_id"] = challenge_id
            step.update(verified=True,challenge_id_hash=hashlib.sha256(challenge_id.encode()).hexdigest())
        step_up_ms = (time.perf_counter()-step_started)*1000
        current = self.snapshots(task_id)
        current_pending,current_binding = validated_confirmation_pair(current["customer"],current["trace"],expected_action,
            selected_customer=prepared["customer"])
        current_node = next(item for item in current["trace"]["nodes"] if item["node_id"] == current_binding["node_id"])
        validated_expected_constraints(current_pending,current_node,current["trace"],current["customer"],expected_constraints,validate_scope)
        source_hash = _hash({"trace":current["trace"],"customer":current["customer"],"binding":current_binding})
        started = time.perf_counter()
        submit_outcome = "ACCEPTED"
        try:
            self._request("POST",f"/api/v2/tasks/{task_id}/confirm",json=payload,expected=(202,),internal=True)
        except AcceptanceStopped as error:
            if error.code != "HTTP_TRANSPORT_UNKNOWN_NO_REISSUE":
                raise
            submit_outcome = "UNKNOWN_OBSERVE_ORIGINAL_ONLY"
        confirm_post_ms = self.last_http["elapsed_ms"]
        final = self.settle(task_id,timeout=timeout)
        result = {"prepared_observation":prepared,"trace":final["trace"],"customer":final["customer"],"state":final["state"],
            "task_id":task_id,"CF_binding":binding,"source_hash":source_hash,"step_up":step,"submit_outcome":submit_outcome,
            "prepared_source_hash":prepared_source_hash,
            "timing":{"step_up_ms":round(step_up_ms,3),"execution_ms":round((time.perf_counter()-started)*1000,3),
                "confirm_post_ms":confirm_post_ms}}
        self.save_json("confirmation-result",result)
        return result

    @_record_failure
    def cancel(self, observation, *, reason="acceptance cleanup"):
        require(isinstance(observation,dict), "TASK_OBSERVATION_REQUIRED")
        task_id = observation.get("task_id") or (observation.get("trace") or {}).get("task_id")
        require(task_id in self._created_tasks and task_id not in self._attempted_cancel
            and not any(key[0] == task_id for key in self._attempted_confirmations), "CLEANUP_ONLY_NEW_UNCONFIRMED_TASK")
        prepared = self.snapshots(task_id)
        trace, customer = prepared["trace"], prepared["customer"]
        require(trace.get("state") in {"AWAITING_CONFIRMATION","NEED_CLARIFICATION"}
            and exact_json(trace.get("version"),customer.get("version"))
            and trace.get("state") == customer.get("state") and type(trace.get("version")) is int,
            "CLEANUP_CANNOT_HIDE_COMPLETED_FAILED_OR_UNKNOWN_OPERATION")
        require(not trace.get("receipt"), "CLEANUP_CANNOT_HIDE_COMPLETED_RECEIPT")
        completed_reads = validated_cleanup_read_completions(trace)
        self._attempted_cancel.add(task_id)
        self.event("pending_cleanup",task_id=task_id,reason=reason,version=trace["version"],
            preserved_completed_read_operations=sorted(completed_reads))
        self._request("POST",f"/api/v2/tasks/{task_id}/cancel",json={"expected_task_version":trace["version"]},internal=True)
        result = self.settle(task_id)
        self.save_json("cleanup-result",result)
        return result
