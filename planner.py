"""Pure question selection from YAML and the validated state."""
from conditions import matches, answer_fields

class Planner:
    def __init__(self, config):
        self.config = config

    def current(self, state):
        return self.config.question_map.get(state.current_question_id)

    def limit(self, question):
        return question.max_answer_attempts or self.config.data['flow']['max_answer_attempts']

    def retain(self, state):
        current = self.current(state)
        return bool(current and matches(current.ask_when, state) and state.visits[-1].failures < self.limit(current))

    def select(self, state):
        if self.retain(state):
            return self.current(state), []
        completed = []
        for phase in self.config.data['flow']['phases']:
            if phase.get('resolve_when') and (getattr(state, phase['completion_flag']) or matches(phase['resolve_when'], state)):
                completed.append(phase)
                continue
            visits = [visit for visit in state.visits if visit.phase == phase['id']]
            if phase.get('max_questions') is not None and len(visits) >= phase['max_questions']:
                completed.append(phase)
                continue
            candidates = [question for question in self.config.questions
                          if question.phase == phase['id'] and matches(question.ask_when, state)
                          and self.can_open(question, state)]
            if candidates:
                return max(candidates, key=lambda question: question.priority), completed
            completed.append(phase)
        return None, completed

    def can_open(self, question, state):
        visits = [visit for visit in state.visits if visit.question_id == question.id]
        if not visits:
            return True
        last = visits[-1]
        if last.closed_version is None:
            return state.current_question_id == question.id and self.retain(state)
        return any(state.answers[key].status == 'missing' and state.field_versions.get(key, 0) > last.closed_version
                   for key in answer_fields(question.ask_when))

    def blocking(self, state):
        statuses = self.config.data['flow']['blocking_statuses']
        return [spec.id for spec in self.config.fields if spec.required
                and matches(spec.applicable_when, state) and state.answers[spec.id].status in statuses]

    def missing_parts(self, question, state):
        return [part for part in question.parts if matches(part.ask_when, state)]

    def wording_spec(self, question, state):
        parts = self.missing_parts(question, state)
        # Keep the original full question when all of its collection parts are missing.
        wording = question.base_question if not parts or len(parts) == len(question.parts) else ' '.join(part.text for part in parts)
        return question.model_copy(update={'base_question': wording, 'parts': parts})

    def response_progress(self, question, before, after):
        # Resolve any displayed missing part, without treating explicit uncertainty
        # as resolution of a part whose configured condition remains unresolved.
        return any(not matches(part.ask_when, after)
                   for part in self.missing_parts(question, before))
