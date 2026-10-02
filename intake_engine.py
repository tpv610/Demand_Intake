"""One serialized, version-checked transaction for every user action."""
from threading import Lock
from context_manager import ContextManager
from planner import Planner
from models import Update
from demand_service import DemandService
from rag_service import RAGService

class BusyError(RuntimeError):
    pass

class StaleError(RuntimeError):
    pass

class IntakeEngine:
    def __init__(self, config, llm, jira, rag=None):
        self.config, self.llm, self.jira = config, llm, jira
        self.manager = ContextManager(config)
        self.planner = Planner(config)
        self.output = DemandService(config)
        self.rag = rag if rag is not None else RAGService(config)
        self._state = self.manager.initial()
        self._lock = Lock()

    @property
    def busy(self):
        return self._lock.locked()

    def view(self):
        # Committed states are never modified. Readers get an independent copy.
        return self._state.model_copy(deep=True)

    def execute(self, version, work):
        if not self._lock.acquire(blocking=False):
            raise BusyError(self.config.text('busy'))
        try:
            if version != self._state.version:
                raise StaleError(self.config.text('stale'))
            draft = self.manager.draft(self._state)
            result = work(draft)
            self.manager.commit(self, draft)
            return result
        finally:
            self._lock.release()

    def submit_text(self, version, text):
        text = text.strip()
        if not text:
            raise ValueError(self.config.text('empty'))
        def work(draft):
            before = self._state
            interpretation = self.llm.interpret(text, before.model_copy(deep=True))
            validation = self.manager.apply(draft, interpretation.updates, 'llm')
            question = self.planner.current(before)
            progress = bool(question and self.planner.response_progress(question, before, draft))
            self.manager.record_response(draft, text, interpretation.answers_current_question, progress)
            self.manager.record_ambiguities(draft, interpretation.ambiguities, text)
            self.advance(draft, refresh=interpretation.answers_current_question)
            return validation
        return self.execute(version, work)

    def submit_selection(self, version, question_id, option_id):
        def work(draft):
            question = self.planner.current(draft)
            if not question or question.id != question_id:
                raise StaleError(self.config.text('stale_selection'))
            option = next((option for option in question.input.options if option.id == option_id and option.kind == 'value'), None)
            if option is None:
                raise ValueError(self.config.text('invalid_choice'))

            self.manager.apply(draft, [Update(field_id=question.input.target_field,
                value=option.value, status=option.status, evidence=option.label)], 'ui_selection')
            progress = self.planner.response_progress(question, self._state, draft)
            self.manager.record_response(draft, option.label, True, progress)
            self.advance(draft)
        return self.execute(version, work)

    def advance(self, draft, refresh=False):
        retained = self.planner.retain(draft)
        if not retained:
            self.manager.close_current(draft)
        question, phases = self.planner.select(draft)
        self.manager.finish_phases(draft, phases)
        if question is None:
            self.manager.set_knowledge_notice(draft, '')
            self.manager.close_current(draft)
            return
        if retained and not refresh and draft.answers == self._state.answers and draft.pending_ambiguities == self._state.pending_ambiguities:
            return
        self.manager.set_knowledge_notice(draft, '')
        wording = self.planner.wording_spec(question, draft)
        text, sources, passages = wording.base_question, [], []
        if (question.rag_search and self.config.data['rag']['enabled']
                and draft.answers[self.config.data['flow']['domain_field']].status == 'answered'):
            try:
                passages = self.rag.search(draft, wording)
            except Exception:
                self.manager.set_knowledge_notice(draft, self.config.text('rag_unavailable'))
        used = []
        if passages:
            try:
                text, used = self.llm.contextualize(wording, draft, passages)
            except Exception:
                # Wording is optional: accepted facts and the configured question remain usable.
                text = wording.base_question
        sources = [dict(chunk_id=passage['chunk_id'], title=passage['title'],
                        source=passage['source'], version=passage['version'], synthetic=passage['synthetic'])
                   for passage in passages if passage['chunk_id'] in used]
        self.manager.show(draft, question, text, sources, passages)

    def confirm(self, version):
        def work(draft):
            if draft.current_question_id or self.planner.blocking(draft):
                raise ValueError(self.config.text('not_ready'))
            self.manager.confirm(draft)
        return self.execute(version, work)

    def generate(self, version):
        def work(draft):
            if not draft.confirmed or draft.current_question_id or self.planner.blocking(draft):
                raise ValueError(self.config.text('not_ready'))
            if draft.package:
                return draft.package
            route = self.output.route(draft)
            raw = self.llm.generate(draft, route)
            package = self.output.build(draft, raw, route)
            issue = self.jira.create(package, self.output.markdown(package))
            self.manager.store_package(draft, package, issue)
            return package
        return self.execute(version, work)
