"""The sole writer of business state. Every turn works on a private draft."""
from collections import Counter
from models import Answer, Context, Visit
from conditions import matches

class ContextManager:
    def __init__(self, config):
        self.config = config

    def initial(self):
        state = Context(answers={field.id: Answer() for field in self.config.fields})
        first = self.config.question_map[self.config.data['flow']['initial_question_id']]
        self.show(state, first, first.base_question)
        return state

    def set_answer(self, state, key, answer):
        previous = state.answers[key]
        if (previous.value, previous.status) == (answer.value, answer.status):
            return False
        state.answers[key] = answer
        state.field_versions[key] = state.version
        return True

    def apply(self, state, updates, source):
        accepted, rejected, changed = [], [], []
        counts = Counter(update.field_id for update in updates)
        for update in updates:
            spec = self.config.field_map.get(update.field_id)
            reason = None
            if spec is None:
                reason = 'Unknown field'
            elif counts[update.field_id] != 1:
                reason = 'Duplicate updates for one field'
            elif not update.supported or not update.evidence.strip():
                reason = 'Unsupported update or missing evidence'
            elif not self.valid_value(spec, update):
                reason = 'Value does not match field type, status or allowed values'
            if reason:
                rejected.append({'field': update.field_id, 'reason': reason})
                continue
            accepted.append(update.field_id)
            if self.set_answer(state, update.field_id, Answer(value=update.value, status=update.status,
                                                             evidence=update.evidence, source=source)):
                changed.append(update.field_id)
        direct = list(changed)
        for rule in self.config.data['invalidation']:
            if rule['field'] in direct:
                for key in rule['clear']:
                    if key not in accepted and self.set_answer(state, key, Answer(source='dependency')):
                        changed.append(key)
        for spec in self.config.fields:
            if spec.not_applicable_when and matches(spec.not_applicable_when, state):
                if self.set_answer(state, spec.id, Answer(status='not_applicable', source='dependency')):
                    changed.append(spec.id)
            elif state.answers[spec.id].status == 'not_applicable' and state.answers[spec.id].source == 'dependency':
                if self.set_answer(state, spec.id, Answer(source='dependency')):
                    changed.append(spec.id)
        if changed:
            state.confirmed = False
            state.package = None
            state.issue = None
        state.pending_ambiguities = [item for item in state.pending_ambiguities
                                     if all(state.answers[key].status == 'missing' for key in item.field_ids)]
        return {'captured': direct, 'changed': list(dict.fromkeys(changed)), 'rejected': rejected}

    @staticmethod
    def valid_value(spec, update):
        value = update.value
        if update.status != 'answered':
            return value is None
        if spec.type == 'boolean':
            return type(value) is bool
        if spec.type == 'integer':
            return type(value) is int and value >= 0
        if spec.type == 'number':
            return type(value) in {int, float}
        if spec.type == 'list':
            return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)
        return isinstance(value, str) and bool(value.strip()) and (not spec.allowed_values or value in spec.allowed_values)

    def record_response(self, state, message, counts, progress):
        state.messages.append({'role': 'user', 'content': message})
        if state.current_question_id:
            visit = state.visits[-1]
            visit.failures = 0 if progress else visit.failures + int(counts)

    def close_current(self, state):
        if state.current_question_id:
            state.visits[-1].closed_version = state.version
        state.current_question_id = None
        state.current_question_text = None
        state.phase = ''
        state.current_knowledge = []

    def finish_phases(self, state, phases):
        for phase in phases:
            if phase.get('completion_flag'):
                setattr(state, phase['completion_flag'], True)

    def show(self, state, question, text, sources=None, passages=None):
        if state.current_question_id != question.id:
            state.visits.append(Visit(question_id=question.id, phase=question.phase,
                                      opened_version=state.version))
            phase = next(item for item in self.config.data['flow']['phases'] if item['id'] == question.phase)
            if phase.get('attempt_counter'):
                key = phase['attempt_counter']
                setattr(state, key, getattr(state, key) + 1)
        state.current_knowledge = list(passages or [])
        state.current_question_id = question.id
        state.current_question_text = text
        state.phase = question.phase
        state.messages.append({'role': 'assistant', 'content': text, 'knowledge_sources': sources or []})

    def confirm(self, state):
        state.confirmed = True

    def store_package(self, state, package, issue):
        state.package, state.issue = package, issue

    def commit(self, owner, draft):
        owner._state = draft

    def draft(self, state):
        draft = state.model_copy(deep=True)
        draft.version += 1
        return draft


    def set_knowledge_notice(self, state, notice):
        state.rag_notice = notice

    def record_ambiguities(self, state, ambiguities, message):
        retained = [item for item in state.pending_ambiguities
                    if all(state.answers[key].status == 'missing' for key in item.field_ids)]
        for item in ambiguities:
            if not item.evidence.strip() or item.evidence.strip().casefold() not in message.casefold():
                continue
            if not item.field_ids or any(key not in state.answers for key in item.field_ids):
                continue
            fields = [key for key in item.field_ids if state.answers[key].status == 'missing']
            if fields and item.clarification_need.strip():
                retained = [old for old in retained if not set(old.field_ids).intersection(fields)]
                retained.append(item.model_copy(update={'field_ids': fields}))
        state.pending_ambiguities = retained
