import pytest
from models import Ambiguity
from conditions import matches
from tests.conftest import complete, submit, update

@pytest.mark.parametrize('domain,team',[('HR','HR Operations'),('Finance','Finance Operations'),('Retail','Generic Operations')])
def test_complete_initial_message_skips_questions_and_routes(system,domain,team):
    _,llm,engine=system
    submit(llm,engine,complete(domain))
    assert engine.view().current_question_id is None
    assert len(llm.calls)==1
    engine.confirm(engine.view().version)
    engine.generate(engine.view().version)
    assert engine.view().package['routing_destination']==team

def test_opening_domain_and_application_order(system):
    _,llm,engine=system
    submit(llm,engine,[update('requested_change','Upload customer spreadsheet')])
    assert engine.view().current_question_id=='business_need'
    submit(llm,engine,[update('business_need','The capability is absent')])
    assert engine.view().current_question_id=='primary_users'
    submit(llm,engine,[update('primary_users',['Business users'])])
    assert engine.view().current_question_id=='domain_function'
    submit(llm,engine,[update('domain',status='unconfirmed')])
    assert engine.view().current_question_id=='domain_process'
    submit(llm,engine,[update('domain',status='unconfirmed')])
    assert engine.view().current_question_id=='application_context'
    assert engine.view().domain_clarification_attempts==2
    assert engine.view().answers['domain'].status=='unconfirmed'

def test_partial_answer_uses_yaml_part_without_second_call(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete('Retail') if u.field_id not in ('target_timeline','timing_reason')])
    count=len(llm.calls)
    submit(llm,engine,[update('target_timeline','June 2027')])
    state=engine.view()
    assert state.current_question_text=='What is driving that timing?'
    assert len(llm.calls)==count+1
    assert state.visits[-1].failures==0
    assert sum(v.question_id=='delivery_context' for v in state.visits)==1
    submit(llm,engine,[])
    assert engine.view().visits[-1].failures==1
    assert engine.view().current_question_id=='delivery_context'
    submit(llm,engine,[update('timing_reason','Customer request')])
    assert engine.view().current_question_id is None

def test_unrelated_domain_correction_retains_question_and_rewords(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete('Admin') if u.field_id!='success_result'])
    submit(llm,engine,[update('domain','HR')],answers_current_question=False)
    assert engine.view().current_question_id=='success_result'
    assert engine.view().current_question_text==engine.config.question_map['success_result'].base_question
    assert engine.view().visits[-1].failures==0
    assert engine.view().answers['success_result'].status=='missing'

def test_unknown_delivery_does_not_repeat(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id not in ('target_timeline','timing_reason')])
    submit(llm,engine,[update('target_timeline',status='unconfirmed'),update('timing_reason',status='unconfirmed')])
    assert engine.view().current_question_id is None

def test_dependency_change_clears_stale_details(system):
    _,llm,engine=system
    submit(llm,engine,complete()+[update('integration_required',True)])
    # Duplicate proposed controller updates are rejected, then explicitly supplied.
    submit(llm,engine,[update('integration_required',True),update('integration_systems',['ERP']),update('integration_flow','Read customer information'),update('integration_details','Customer details read from ERP')])
    submit(llm,engine,[update('integration_required',False)])
    assert engine.view().answers['integration_systems'].status=='not_applicable'
    assert engine.view().answers['integration_systems'].value is None

def test_retraction_reopens_resolved_question(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='business_owner'])
    submit(llm,engine,[update('business_owner','HR Lead')])
    assert engine.view().current_question_id is None
    submit(llm,engine,[update('business_owner',status='missing')])
    assert engine.view().current_question_id=='owner'

def test_unknown_controller_never_sets_related_details(system):
    _,llm,engine=system
    submit(llm,engine,[update('integration_required',status='unconfirmed'),update('application_context',status='unconfirmed')])
    assert all(engine.view().answers[key].status=='missing' for key in ('integration_systems','integration_flow','existing_application'))

def test_qualitative_success_and_capability_gap_are_valid_values(system):
    _,llm,engine=system
    submit(llm,engine,[update('business_need','The requested capability is absent'),update('success_result','Better user experience')])
    assert engine.view().answers['business_need'].status=='answered'
    assert engine.view().answers['success_result'].status=='answered'

def test_scope_unclear_remains_pending(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='organizational_scope'])
    submit(llm,engine,[],'very widely',ambiguities=[Ambiguity(field_ids=['organizational_scope'],evidence='very widely',clarification_need='Clarify the organizational boundaries')])
    assert engine.view().answers['organizational_scope'].status=='missing'
    assert engine.view().current_question_id=='organizational_scope'

def test_false_is_answered(system):
    _,llm,engine=system
    submit(llm,engine,[update('integration_required',False)])
    assert matches({'field':'answers.integration_required','operator':'answered'},engine.view())
    assert not matches({'field':'answers.integration_required','operator':'missing'},engine.view())

def test_priority_is_not_copied_to_acceptance(system):
    _,llm,engine=system
    submit(llm,engine,complete()+[update('supplied_priority','Critical')])
    engine.confirm(engine.view().version);engine.generate(engine.view().version)
    package=engine.view().package
    assert package['confirmed_context']['answers']['supplied_priority']['value']=='Critical'
    assert 'Critical' not in next(s['body'] for s in package['sections'] if s['id']=='acceptance')

def test_repeated_generate_reuses_package_and_issue(system):
    _,llm,engine=system
    submit(llm,engine,complete());engine.confirm(engine.view().version)
    engine.generate(engine.view().version)
    key=engine.view().issue['key']
    engine.generate(engine.view().version)
    assert engine.view().issue['key']==key
    assert len([c for c in llm.calls if c[0]=='generate'])==1

def test_correction_requires_new_confirmation(system):
    _,llm,engine=system
    submit(llm,engine,complete());engine.confirm(engine.view().version);engine.generate(engine.view().version)
    submit(llm,engine,[update('business_owner','New owner')])
    assert not engine.view().confirmed
    assert engine.view().package is None
    with pytest.raises(ValueError):engine.generate(engine.view().version)
