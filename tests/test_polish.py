from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.conftest import complete, submit, update


def test_uncertain_scope_gets_one_clarification_and_can_be_deferred(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='organizational_scope'])
    submit(llm,engine,[update('organizational_scope',status='unconfirmed')],'Across all teams')
    state=engine.view()
    assert state.current_question_id=='scope_clarification'
    engine.submit_selection(state.version,'scope_clarification','undecided')
    assert engine.view().current_question_id is None
    assert engine.view().answers['organizational_scope'].status=='unconfirmed'


def test_scope_clarification_resolves_with_deterministic_selection(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='organizational_scope'])
    submit(llm,engine,[update('organizational_scope',status='unconfirmed')])
    engine.submit_selection(engine.view().version,'scope_clarification','department')
    assert engine.view().answers['organizational_scope'].value=='department'
    assert engine.view().current_question_id is None


def test_charter_and_generated_cleanup(system):
    _,llm,engine=system
    values=[u for u in complete() if u.field_id not in {'target_timeline','timing_reason','business_owner'}]
    submit(llm,engine,values+[update('target_timeline','July 2040'),update('timing_reason','Contractual commitments'),
                              update('business_owner','Payments Processing Team')])
    engine.confirm(engine.view().version)
    draft=llm.generate(engine.view(),'Finance Operations')
    draft['sections']['story']='As an AR specialist, I want matching so that work is reduced.\n\nAs the system, calculate gains.'
    draft['sections']['acceptance']='- Match incoming wires to open invoices.\n- Detailed matching decisions require refinement.'
    package=engine.output.build(engine.view(),draft,'Finance Operations')
    charter=next(s['body'] for s in package['sections'] if s['id']=='charter')
    assert all(v in charter for v in ['July 2040','Contractual commitments','Payments Processing Team'])
    story=next(s['body'] for s in package['sections'] if s['id']=='story')
    assert 'As the system' not in story
    acceptance=next(s['body'] for s in package['sections'] if s['id']=='acceptance')
    assert 'require refinement' not in acceptance
    assert 'Detailed matching decisions require refinement.' in package['follow_up_items']


def test_classification_accepts_multiple_grounded_sources_and_shows_unknown(system):
    _,llm,engine=system
    submit(llm,engine,complete()+[update('constraints',['Real-time rates required'])])
    assessment={'urgency':'High','urgency_evidence':[{'field_id':'business_owner','quote':'Invented'}],
                'complexity':'Medium','complexity_evidence':[
                    {'field_id':'requested_change','quote':'Add request tracking'},
                    {'field_id':'constraints','quote':'Real-time rates required'}]}
    rows=engine.output.classification(engine.view(),assessment)
    assert {'attribute':'Complexity','value':'Medium (initial estimate)'} in rows
    assert {'attribute':'Urgency','value':'Not confirmed'} in rows


def test_interview_hides_reference_details(system):
    _,_,engine=system
    # References remain in state for interpretation, never in visible interview widgets.
    engine._state.messages[-1]['knowledge_sources']=[{'title':'Financial reconciliation synthetic demo'}]
    engine._state.current_knowledge=[{'title':'Hidden reference title','content':'Hidden passage'}]
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'))
    app.session_state['engine']=engine
    app.session_state['jira']=engine.jira
    app.run()
    assert not app.exception
    assert not any('reference' in c.value.lower() for c in app.caption)
    assert not any('reference' in e.label.lower() for e in app.expander)
    assert not any('Hidden passage' in m.value for m in app.markdown)
