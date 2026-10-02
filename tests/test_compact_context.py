import json
from types import SimpleNamespace
from models import Interpretation, Ambiguity
from llm_service import LLMService
from tests.conftest import complete, update, submit

def capture(config,state,message='Latest answer'):
    calls=[]
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=Interpretation())
    service=LLMService(config,SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    service.interpret(message,state)
    return service,json.loads(calls[0]['input'][1]['content'])

def test_interpretation_sends_no_history_or_missing_answers(system):
    config,_,engine=system
    state=engine.view()
    state.messages=[{'role':'user','content':'old text '*1000}]
    _,payload=capture(config,state)
    assert 'recent_conversation' not in payload
    assert payload['captured_answers']=={}
    assert payload['current_question']['text']==state.current_question_text
    assert not {'questions','flow','visits','version','context'}.intersection(payload)
    assert len(payload['field_definitions'])==len(config.fields)

def test_current_question_reference_context_reaches_interpreter_separately(system):
    config,_,engine=system
    state=engine.view()
    state.current_knowledge=[{'chunk_id':'hr_leave:0:0','domain':'HR','title':'Synthetic leave',
                              'source':'hr.yaml','version':'1.0','synthetic':True,
                              'content':'Demo HR Portal is a possible application, not a confirmed answer.'}]
    _,payload=capture(config,state,'Yes, that portal')
    assert payload['reference_passages'][0]['chunk_id']=='hr_leave:0:0'
    assert 'existing_application' not in payload['captured_answers']

def test_invalid_ambiguity_does_not_create_question_or_state_fact(system):
    _,llm,engine=system
    ambiguities=[Ambiguity(field_ids=['invented'],evidence='widely',clarification_need='Invented question'),
                 Ambiguity(field_ids=['organizational_scope'],evidence='not in message',clarification_need='Unsupported')]
    submit(llm,engine,[u for u in complete() if u.field_id!='organizational_scope'],'widely',ambiguities=ambiguities)
    assert engine.view().pending_ambiguities==[]
    assert engine.view().current_question_id=='organizational_scope'

def test_supported_ambiguity_clears_on_resolution(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='organizational_scope'])
    submit(llm,engine,[],'widely',ambiguities=[Ambiguity(field_ids=['organizational_scope'],
           evidence='widely',clarification_need='Clarify organization boundaries')])
    assert engine.view().pending_ambiguities
    submit(llm,engine,[update('organizational_scope','multiple_departments')],'Multiple departments')
    assert not engine.view().pending_ambiguities
    assert engine.view().current_question_id is None

def test_partial_integration_asks_only_missing_information(system):
    config,llm,engine=system
    values=[u for u in complete('IT') if u.field_id!='integration_required']
    values+=[update('integration_required',True),update('integration_systems',['GitHub']),
             update('integration_flow','This capability sends data to GitHub')]
    submit(llm,engine,values)
    state=engine.view()
    assert state.current_question_id=='integration_details'
    parts=engine.planner.missing_parts(config.question_map['integration_details'],state)
    assert [part.id for part in parts]==['information_and_action']
    assert state.current_question_text==parts[0].text


def test_scope_only_answer_to_combined_audience_question_counts_as_progress(system):
    _,llm,engine=system
    submit(llm,engine,[update('requested_change','Task tracking'),update('business_need','Track task progress')])
    assert engine.view().current_question_id=='primary_users'
    submit(llm,engine,[update('organizational_scope','department')],'One department')
    assert engine.view().current_question_text=='Who will use this capability?'
    assert engine.view().visits[-1].failures==0


def test_generation_excludes_application_only_configuration(system):
    config,_,engine=system
    calls=[]
    def parse(**kwargs):
        calls.append(json.loads(kwargs['input'][1]['content']))
        schema=kwargs['text_format']
        return SimpleNamespace(output_parsed=schema.model_validate({
            'title':'Capability',
            'sections':{section['id']:'Text' for section in config.data['output']['generated_sections']},
            'classification_assessment':{'urgency':'Not confirmed','complexity':'Not confirmed',
                                         'urgency_evidence':[],'complexity_evidence':[]},
            'refinement_items':[]}))
    service=LLMService(config,SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    service.generate(engine.view(),'HR Operations')
    payload=calls[0]
    assert 'output_configuration' not in payload
    assert set(payload['generation_policy'])=={'title_limits','milestone_stages','refinement_owned_fields'}
    assert payload['confirmed_context']['answers']=={}
