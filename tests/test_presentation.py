from models import Answer
from tests.conftest import complete, submit, update

def test_public_package_hides_objective_and_optional_omissions(system):
    config, llm, engine = system
    submit(llm, engine, complete())
    engine.confirm(engine.view().version)
    engine.generate(engine.view().version)
    state = engine.view()
    assert 'business_objective' not in config.field_map
    assert 'business_objective' not in state.package['confirmed_context']['answers']
    markdown = engine.output.markdown(state.package)
    assert 'Business objective' not in markdown and 'Confirmed business context' not in markdown
    assert not any('Estimated users' in item or 'Supplied priority' in item for item in state.package['follow_up_items'])
    assert engine.output.display(Answer(value=True, status='answered'), 'integration_required') == 'Yes'
    assert engine.output.display(state.answers['application_context'], 'application_context') == 'New standalone capability'

def test_integration_and_refinement_preserve_supplied_behavior(system):
    _, llm, engine = system
    updates = [u for u in complete('Loyalty') if u.field_id not in {'integration_required','functional_requirements'}]
    updates += [update('integration_required', True), update('integration_systems', ['Customer Portal']),
                update('integration_flow', 'Receive Customer Portal data to identify prospective repeat customers'),
                update('integration_details', 'Customer profile information is received for repeat-customer identification'),
                update('functional_requirements', ['Award points', 'Redeem points for discounts'])]
    submit(llm, engine, updates)
    engine.confirm(engine.view().version)
    engine.generate(engine.view().version)
    package = engine.view().package
    requirements = next(s['body'] for s in package['sections'] if s['id'] == 'integration')
    assert 'Receive Customer Portal data' in requirements
    assert not any('Confirm the data exchanged' in item for item in package['follow_up_items'])
    table = engine.output.classification_table(package['classification'])
    assert '| Domain | Loyalty |' in table
    assert '| Urgency | Not confirmed |' in table
    assert '| Integration | Yes |' in table

def test_acceptance_preserves_exact_threshold_and_removes_draft_prefix(system):
    _, llm, engine = system
    constraints = ['Suggestions must load in less than 200 milliseconds.',
                   'The tool must not automatically commit or push code to GitHub.']
    submit(llm, engine, complete('IT') + [update('constraints', constraints)])
    engine.confirm(engine.view().version)
    original = llm.generate
    def draft(state, route):
        result = original(state, route)
        result['sections']['acceptance'] = '- Draft: Suggestions appear as the developer types.'
        return result
    llm.generate = draft
    engine.generate(engine.view().version)
    body = next(s['body'] for s in engine.view().package['sections'] if s['id'] == 'acceptance')
    assert 'Draft:' not in body
    assert all(value in body for value in constraints)
    assert not hasattr(engine, 'events')
    assert not hasattr(engine.view(), 'changes')
    assert not any(m['content'].startswith('Updated:') for m in engine.view().messages)

def test_vague_integration_requires_information_and_receiving_action(system):
    _, llm, engine = system
    updates = [u for u in complete('IT') if u.field_id != 'integration_required']
    submit(llm, engine, updates + [update('integration_required', True),
           update('integration_systems', ['GitHub']), update('integration_flow', 'Sends data to GitHub')])
    assert engine.view().current_question_id == 'integration_details'
    submit(llm, engine, [update('integration_details', status='unconfirmed')])
    assert engine.view().current_question_id is None

def test_interface_contains_no_diagnostics_or_timing(system):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    config, llm, engine = system
    submit(llm, engine, complete())
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['engine'] = engine
    app.session_state['jira'] = engine.jira
    app.run()
    assert not app.exception
    assert not app.checkbox
    assert not any('Last operation' in caption.value for caption in app.caption)
