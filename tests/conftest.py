import pytest
from configuration import Configuration
from intake_engine import IntakeEngine
from demand_service import MockJiraService
from models import Interpretation, Update

class StubLLM:
    def __init__(self, config):
        self.config = config
        self.responses = []
        self.calls = []

    def interpret(self, message, state):
        self.calls.append(('interpret', message, state))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def contextualize(self, question, state, passages):
        self.calls.append(('contextualize', question.id, state))
        return question.base_question, [passages[0]['chunk_id']]

    def generate(self, state, routing):
        self.calls.append(('generate', routing, state))
        return {'classification_assessment': {'urgency':'Not confirmed','complexity':'Not confirmed','urgency_evidence':[],'complexity_evidence':[]}, 'refinement_items':[], 'title': 'Business Capability', 'sections': {section['id']: 'Supplied behavior' for section in self.config.data['output']['generated_sections']}}

@pytest.fixture
def system():
    config = Configuration()
    llm = StubLLM(config)
    engine = IntakeEngine(config, llm, MockJiraService(config))
    return config, llm, engine

def update(key, value=None, status='answered'):
    return Update(field_id=key, value=value, status=status, evidence='User supplied this fact')

def complete(domain='HR'):
    return [update('requested_change', 'Add request tracking'), update('business_need', 'Current capability is unavailable'),
            update('primary_users', ['Employees']), update('functional_requirements', ['Submit a request', 'View request status']),
            update('domain', domain), update('application_context', 'standalone'), update('success_result', 'Reduce effort'),
            update('organizational_scope', 'enterprise'), update('integration_required', False),
            update('target_timeline', status='unconfirmed'), update('timing_reason', status='unconfirmed'),
            update('business_owner', status='unconfirmed')]

def submit(llm, engine, updates, text='User answer', **kwargs):
    llm.responses.append(Interpretation(updates=updates, **kwargs))
    return engine.submit_text(engine.view().version, text)
