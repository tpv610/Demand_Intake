import json
from types import SimpleNamespace
import pytest
from llm_service import LLMService
from models import QuestionWording
from tests.conftest import complete, update, submit


def passage():
    return {'chunk_id':'hr_leave:0:0','domain':'HR','title':'Leave systems',
            'source':'hr.yaml','version':'1','synthetic':True,'content':'Workday supplies leave balances.'}


def test_wording_payload_is_scoped_to_question(system):
    config,_,engine=system
    state=engine.view()
    engine.manager.apply(state,[update('domain','HR'),update('requested_change','Check leave balance'),
                                update('business_owner','Unrelated owner')],'test')
    question=engine.planner.wording_spec(config.question_map['existing_application'],state)
    captured=[]
    def parse(**kwargs):
        captured.append(json.loads(kwargs['input'][1]['content']))
        return SimpleNamespace(output_parsed=QuestionWording(question='Is this a change to Workday or another application?',
            field_ids=['existing_application'],source_chunk_ids=['hr_leave:0:0']))
    service=LLMService(config,SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    text,sources=service.contextualize(question,state,[passage()])
    payload=captured[0]
    assert payload['captured_facts']=={'domain':'HR','requested_change':'Check leave balance'}
    assert payload['target_field_ids']==['existing_application']
    assert not {'field_definitions','recent_conversation','context','questions','output_configuration'}.intersection(payload)
    assert sources==['hr_leave:0:0'] and 'Workday' in text


@pytest.mark.parametrize('fields,sources',[(['business_owner'],['hr_leave:0:0']),
                                          (['existing_application'],['invented'])])
def test_invalid_wording_scope_or_source_is_rejected(system,fields,sources):
    config,_,engine=system
    client=SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs:SimpleNamespace(
        output_parsed=QuestionWording(question='Question?',field_ids=fields,source_chunk_ids=sources))))
    with pytest.raises(ValueError):
        LLMService(config,client).contextualize(config.question_map['existing_application'],engine.view(),[passage()])


def test_irrelevant_passages_keep_configured_wording(system):
    config,_,engine=system
    question=config.question_map['existing_application']
    client=SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs:SimpleNamespace(
        output_parsed=QuestionWording(question='Changed wording',field_ids=['existing_application'],source_chunk_ids=[]))))
    assert LLMService(config,client).contextualize(question,engine.view(),[passage()])==(question.base_question,[])


@pytest.mark.parametrize('failure',[False,True])
def test_contextual_question_or_fallback_preserves_facts(system,failure):
    config,llm,engine=system
    engine.rag=SimpleNamespace(search=lambda *args:[passage()])
    def wording(question,state,passages):
        if failure:raise RuntimeError('Provider unavailable')
        return 'Is this a change to Workday or a new standalone capability?', ['hr_leave:0:0']
    llm.contextualize=wording
    submit(llm,engine,[u for u in complete() if u.field_id!='application_context'])
    state=engine.view()
    assert state.current_question_id=='application_context'
    assert state.answers['existing_application'].status=='missing'
    assert state.answers['domain'].value=='HR'
    assert state.current_question_text==(config.question_map['application_context'].base_question if failure
                                        else 'Is this a change to Workday or a new standalone capability?')
