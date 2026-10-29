"""Owned, versioned recipient choices. A choice fills identity, never authorizes money."""
from copy import deepcopy
from datetime import timedelta
import secrets

from sqlalchemy import select

from .access_models import LoginAccount
from .authentication import as_utc
from .contact_models import ContactAlias
from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Account, Card, Payee, Task, utcnow
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeRecord, RuntimeOperation

KEY = 'recipient_choice_set_v1'
SELECTION = 'recipient_choice_selection_v1'
KIND = 'OWNED_RECIPIENT_CHOICE'
VERSION = 'recipient-choice-v1'
KINDS = {'TRANSFER', 'TRANSFER_SCHEDULE', 'FX_TRANSFER'}
FIELDS = ('payee_name', 'recipient_card_reference')


def _deny(code='RECIPIENT_CHOICE_STALE'):
    raise DomainError(code, '收款选项已变化，请重新查看后选择。', 409)


def _owned(row, principal):
    return row is not None and (row.sandbox_id, row.user_id) == (principal.sandbox_id, principal.user_id)


def _scope(session, principal, thread_id, origin_id, *, lock=False):
    from .conversation_service import owned_thread
    from .permissions import require_banking_actor
    if require_banking_actor(session, principal) not in {'CUSTOMER', 'DEV_TEST'}:
        _deny('RECIPIENT_CHOICE_OWNER_REQUIRED')
    thread = owned_thread(session, principal, thread_id, lock=lock)
    origin = session.get(ChatTurn, origin_id)
    if not _owned(origin, principal) or origin.thread_id != thread.id:
        _deny('RECIPIENT_CHOICE_NOT_FOUND')
    task = session.get(Task, origin.task_id) if origin.task_id else None
    return thread, origin, task


def _pending_action(session, principal, thread, origin, task):
    if thread.status != 'ACTIVE' or origin.sequence != thread.next_sequence - 1 \
            or origin.status != 'ANSWERED' or not _owned(task, principal) \
            or task.state not in {'NEED_CLARIFICATION', 'AWAITING_CONFIRMATION'}:
        return None
    from .conversation_turn_state import _task_epoch_current
    if not _task_epoch_current(session, principal, task):
        return None
    record = session.get(RuntimeRecord, task.id)
    if not record or record.metadata_json.get('semantic_pause'):
        return None
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
    from .intelligence import READ_KINDS
    if any(op.action_kind not in READ_KINDS and op.status in {'AUTHORIZED', 'RUNNING', 'SUCCEEDED', 'UNKNOWN'} for op in operations):
        return None
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record.plan_version else None
    proposal = snapshot.proposal_json if snapshot else (record.metadata_json.get('cloud_semantic_intent') or {}).get('expected_proposal', {})
    matches = [(i, action) for i, action in enumerate(proposal.get('actions', [])) if action.get('kind') in KINDS]
    if len(matches) != 1:
        return None
    ordinal, action = matches[0]
    slots = action.get('slots') or {}
    if not isinstance(slots.get('payee_name'), str) or slots.get('recipient_card_reference') or slots.get('destination_card_reference'):
        return None
    return ordinal, action


def _candidates(session, principal, reference):
    """Only owned saved relationships, or an exact account the user supplied."""
    from .internal_banking_models import InternalPayeeBinding
    from .card_identifiers import card_identifier_view
    aliases = list(session.scalars(select(ContactAlias).where(ContactAlias.sandbox_id == principal.sandbox_id,
        ContactAlias.user_id == principal.user_id, ContactAlias.status == 'ACTIVE')))
    named = [a for a in aliases if a.alias_name == reference]
    logins = {}
    for a in named:
        login = session.get(LoginAccount, a.recipient_login_account_id)
        if login: logins[login.id] = login
    # Exact username is an explicitly named identity, never prefix/name enumeration.
    exact = session.scalar(select(LoginAccount).where(LoginAccount.username == reference.lower()))
    if exact and exact.user_id != principal.user_id:
        logins[exact.id] = exact
    payees = list(session.scalars(select(Payee).where(Payee.sandbox_id == principal.sandbox_id,
        Payee.user_id == principal.user_id, Payee.status == 'ACTIVE', Payee.display_name == reference)))
    for payee in payees:
        bound = session.get(InternalPayeeBinding, payee.id)
        if bound:
            login = session.get(LoginAccount, bound.recipient_login_account_id)
            if login: logins[login.id] = login
    result = []
    for login in sorted(logins.values(), key=lambda row: row.id):
        if login.status != 'ACTIVE' or login.role not in {'CUSTOMER', 'DEV_TEST'} or login.user_id == principal.user_id:
            continue
        owned = [a for a in aliases if a.recipient_login_account_id == login.id]
        allowed_banks = {a.recipient_bank_account_id for a in owned}
        # Knowing a username does not grant an inventory of its private cards.
        # Only its designated receiving instrument and this payer's saved links
        # may be offered; the existing resolver applies active account gates.
        designated_card = None
        designated_version = None
        if login == exact:
            from .payment_instruments import resolve_recipient_instrument
            try:
                designated = resolve_recipient_instrument(session, login.username, caller_principal=principal)
                designated_card, designated_version = designated['card_id'], designated.get('default_version')
            except DomainError:
                pass
        for payee in payees:
            binding = session.get(InternalPayeeBinding, payee.id)
            if binding and binding.recipient_login_account_id == login.id:
                allowed_banks.add(binding.recipient_bank_account_id)
        for card in session.scalars(select(Card).where(Card.sandbox_id == login.sandbox_id,
                Card.user_id == login.user_id, Card.status == 'ACTIVE').order_by(Card.id)):
            if card.account_id not in allowed_banks and card.id != designated_card:
                continue
            bank = session.get(Account, card.account_id)
            if not bank or (bank.sandbox_id, bank.user_id) != (login.sandbox_id, login.user_id):
                continue
            view = card_identifier_view(session, card) or {}
            if not view.get('card_number'): continue
            relations = [{'id': a.id, 'version': a.version} for a in owned if a.recipient_bank_account_id == card.account_id]
            title = reference if named else getattr(login, 'display_name', None) or login.username
            item = {'recipient_login_id': login.id, 'recipient_login_version': login.version,
                'recipient_username': login.username, 'card_id': card.id, 'card_version': card.version,
                'account_id': bank.id, 'card_number': view['card_number'], 'tail': view['display_last4'],
                'name': title[:60], 'relations': relations, 'reference': reference,
                'currency': bank.currency, 'exact_username': login == exact}
            if card.id == designated_card:
                item['default_version'] = designated_version
            item['candidate_id'] = canonical_hash(item)
            result.append(item)
    return result[:12]


def _public(stored):
    return {'version': VERSION, 'choice_set_id': stored['id'], 'task_id': stored['task_id'],
        'task_version': stored['task_version'], 'origin_turn_id': stored['origin_turn_id'],
        'expires_at': stored['expires_at'], 'choices': [
            {'choice_token': row['token'], 'name': row['name'], 'card_last4': row['tail'],
             'currency': row['currency'], 'label': f"选择{row['name']}尾号{row['tail']}的收款卡"}
            for row in stored['candidates']]}


def issue_choices(session, principal, thread_id, origin_id):
    thread, origin, task = _scope(session, principal, thread_id, origin_id, lock=True)
    selected = _pending_action(session, principal, thread, origin, task)
    if selected is None: return {'version': VERSION, 'choices': []}
    ordinal, action = selected
    candidates = _candidates(session, principal, action['slots']['payee_name'])
    if not candidates: return {'version': VERSION, 'choices': []}
    from .dialogue_state import dialogue_actor_scope
    actor_hash = dialogue_actor_scope(session, principal)[1]
    fingerprint = canonical_hash(candidates)
    prior = (origin.diagnostics_json or {}).get(KEY)
    if isinstance(prior, dict) and prior.get('task_version') == task.version \
            and prior.get('actor_hash') == actor_hash and prior.get('fingerprint') == fingerprint \
            and as_utc(utcnow()).isoformat() < prior.get('expires_at', ''):
        return _public(prior)
    stored = {'id': secrets.token_hex(16), 'origin_turn_id': origin.id, 'thread_id': thread.id,
        'task_id': task.id, 'task_version': task.version, 'ordinal': ordinal, 'kind': action['kind'],
        'actor_hash': actor_hash, 'reference': action['slots']['payee_name'], 'fingerprint': fingerprint,
        'expires_at': (utcnow() + timedelta(minutes=10)).isoformat(),
        'candidates': [{**row, 'token': secrets.token_urlsafe(24)} for row in candidates]}
    origin.diagnostics_json = {**(origin.diagnostics_json or {}), KEY: stored}
    session.flush()
    return _public(stored)


def accept_selection(session, principal, thread, value, message, current_task_id, expected_task_version):
    if not isinstance(value, dict) or set(value) != {'origin_turn_id', 'choice_set_id', 'choice_token'}:
        _deny('RECIPIENT_CHOICE_INVALID')
    _, origin, task = _scope(session, principal, thread.id, value['origin_turn_id'])
    if _pending_action(session, principal, thread, origin, task) is None:
        _deny()
    stored = (origin.diagnostics_json or {}).get(KEY) or {}
    from .dialogue_state import dialogue_actor_scope
    if stored.get('id') != value['choice_set_id'] or stored.get('task_version') != task.version \
            or current_task_id != task.id or expected_task_version != task.version \
            or stored.get('actor_hash') != dialogue_actor_scope(session, principal)[1] \
            or utcnow().isoformat() >= stored.get('expires_at', ''):
        _deny()
    matches = [row for row in stored['candidates'] if secrets.compare_digest(row['token'], value['choice_token'])]
    if len(matches) != 1: _deny('RECIPIENT_CHOICE_INVALID')
    candidate = matches[0]
    if message != f"选择{candidate['name']}尾号{candidate['tail']}的收款卡":
        _deny('RECIPIENT_CHOICE_MESSAGE_CHANGED')
    current = _candidates(session, principal, stored['reference'])
    if not any(row == {k: v for k, v in candidate.items() if k != 'token'} for row in current):
        _deny()
    return {'choice_set_id': stored['id'], 'origin_turn_id': origin.id, 'candidate_id': candidate['candidate_id'],
        'task_id': task.id, 'task_version': task.version, 'accepted_at': utcnow().isoformat()}


def attach_choices(session, principal, thread, turn, offered):
    """Publish identities from the previous owned choice set, not arbitrary UI strings."""
    accepted = (turn.diagnostics_json or {}).get(SELECTION)
    origin = session.get(ChatTurn, accepted['origin_turn_id']) if accepted else session.scalar(select(ChatTurn).where(
        ChatTurn.thread_id == thread.id, ChatTurn.sequence == turn.sequence - 1))
    stored = (origin.diagnostics_json or {}).get(KEY) if origin else None
    if not isinstance(stored, dict) or stored.get('thread_id') != thread.id \
            or stored.get('actor_hash') != offered['actor_scope_hash'] \
            or as_utc(turn.created_at).isoformat() >= stored.get('expires_at', ''):
        return
    frame_ref = next((ref for ref, value in offered['frame_bindings'].items() if value.get('task_id') == stored['task_id']), None)
    frame = next((row for row in offered['model']['frames'] if row['frame_ref'] == frame_ref), None)
    if not frame or not frame['continuation_allowed'] or frame['paused']: return
    if offered['frame_bindings'][frame_ref]['task_version'] != stored['task_version']:
        return
    candidates = stored['candidates']
    if accepted:
        candidates = [row for row in candidates if row['candidate_id'] == accepted['candidate_id']]
    for index, candidate in enumerate(candidates):
        if len(offered['model']['entities']) > 38: return
        for field in FIELDS:
            ref = 'e' + str(len(offered['model']['entities']))
            offered['model']['entities'].append({'entity_ref': ref, 'frame_ref': frame_ref,
                'kind': KIND, 'domain': 'TRANSFER', 'label': ('本轮已点击：' if accepted else '可选收款账户：') +
                f"{candidate['name']} · 尾号{candidate['tail']} · {candidate['currency']} · " + field,
                'ordinal': index + 1, 'field': field, 'allowed_fields': [field], 'available_aspects': [],
                'consumer_contract_version': VERSION, 'consumer_kinds': sorted(KINDS)})
            offered['entity_bindings'][ref] = {'source_kind': KIND, 'frame_ref': frame_ref,
                'origin_turn_id': origin.id, 'selection_turn_id': turn.id, 'choice_set_id': stored['id'],
                'candidate': deepcopy({k: v for k, v in candidate.items() if k != 'token'}), 'field': field, 'task_id': stored['task_id'],
                'ordinal': stored['ordinal'], 'kind': stored['kind'], 'explicitly_selected': bool(accepted)}


def reserved_slots(session, thread, turn):
    origin = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.sequence == turn.sequence - 1))
    stored = (origin.diagnostics_json or {}).get(KEY) if origin else None
    if not isinstance(stored, dict): return 0
    return 2 if (turn.diagnostics_json or {}).get(SELECTION) else 2 * min(len(stored.get('candidates', [])), 12)


def checked_identity(session, principal, thread, entry, field, action_kind, *, audit_only=False):
    if field not in FIELDS or field != entry.get('field') or action_kind not in KINDS:
        _deny('RECIPIENT_CHOICE_FIELD_INVALID')
    origin = session.get(ChatTurn, entry.get('origin_turn_id'))
    turn = session.get(ChatTurn, entry.get('selection_turn_id'))
    if not _owned(origin, principal) or not _owned(turn, principal) \
            or origin.thread_id != thread.id or turn.thread_id != thread.id or turn.sequence != origin.sequence + 1:
        _deny('RECIPIENT_CHOICE_NOT_FOUND')
    stored = (origin.diagnostics_json or {}).get(KEY) or {}
    candidate = entry['candidate']
    if stored.get('id') != entry.get('choice_set_id') or candidate not in [
            {k: v for k, v in row.items() if k != 'token'} for row in stored.get('candidates', [])] \
            or as_utc(turn.created_at).isoformat() >= stored.get('expires_at', ''):
        _deny()
    accepted = (turn.diagnostics_json or {}).get(SELECTION)
    if accepted and accepted.get('candidate_id') != candidate['candidate_id']:
        _deny('RECIPIENT_CHOICE_SELECTION_MISMATCH')
    if not audit_only:
        current = _candidates(session, principal, stored['reference'])
        if not any(row == {k: v for k, v in candidate.items() if k != 'token'} for row in current): _deny()
    return candidate['recipient_username'] if field == 'payee_name' else candidate['card_number']


def validate_compiled_choices(decision, offered, actions, refs):
    """A chosen identity must bind the receiver and card as a pair on the same task."""
    used = {}
    for change in decision.get('changes', []):
        entry = offered.get('entity_bindings', {}).get(change.get('entity_ref')) or {}
        if entry.get('source_kind') != KIND: continue
        ordinal = refs.get(change.get('action_ref'))
        if decision['mode'] not in {'CONTINUE', 'REVISE'} or type(ordinal) is not int \
                or ordinal != entry['ordinal'] or decision.get('focus_ref') != entry['frame_ref'] \
                or actions[ordinal]['kind'] != entry['kind']:
            _deny('RECIPIENT_CHOICE_TASK_CHANGED')
        used.setdefault(ordinal, []).append(entry)
    for ordinal, entries in used.items():
        if {row['field'] for row in entries} != set(FIELDS) \
                or len({row['candidate']['candidate_id'] for row in entries}) != 1:
            _deny('RECIPIENT_CHOICE_PAIR_REQUIRED')
        candidate = entries[0]['candidate']; slots = actions[ordinal]['slots']
        if slots.get('payee_name') != candidate['recipient_username'] or slots.get('recipient_card_reference') != candidate['card_number']:
            _deny('RECIPIENT_CHOICE_PAIR_REQUIRED')
    selected = [row for row in offered.get('entity_bindings', {}).values() if row.get('source_kind') == KIND]
    if any(row.get('explicitly_selected') is True for row in selected):
        if not used: _deny('RECIPIENT_CHOICE_NOT_APPLIED')
        # The structured click selects ONLY a recipient identity. Even a valid
        # model literal span in its label cannot change money, memo or funding.
        if any(change.get('field') not in FIELDS or (offered.get('entity_bindings', {}).get(
                change.get('entity_ref')) or {}).get('source_kind') != KIND
                for change in decision.get('changes', [])):
            _deny('RECIPIENT_CHOICE_SCOPE_EXCEEDED')
