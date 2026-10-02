from types import SimpleNamespace
import json
import pytest
from pydantic import ValidationError
from llm_service import LLMService, generation_schema
from models import Interpretation
from tests.conftest import update, submit

def test_generation_schema_requires_exact_configured_properties(system):
    config,_,_=system
    schema=generation_schema(config.data['output'])
    values={s['id']:'Text' for s in config.data['output']['generated_sections']}
    assert schema.model_validate({'title':'Request','sections':values,'classification_assessment':{'urgency':'Not confirmed','complexity':'Not confirmed','urgency_evidence':[],'complexity_evidence':[]},'refinement_items':[]}).sections.model_dump(by_alias=True)==values
    with pytest.raises(ValidationError):schema.model_validate({'title':'Request','sections':{'wrong':'Text'}})
    with pytest.raises(ValidationError):schema.model_validate({'title':'Request','sections':dict(values,extra='Text')})

def test_short_answer_payload_has_displayed_question_and_conversation(system):
    config,_,engine=system
    payloads=[]
    def parse(**kwargs):
        payloads.append(kwargs)
        return SimpleNamespace(output_parsed=Interpretation())
    service=LLMService(config,SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    service.interpret('Short answer',engine.view())
    payload=json.loads(payloads[0]['input'][1]['content'])
    assert payload['current_question']['text']==engine.view().current_question_text
    assert 'recent_conversation' not in payload
    assert not {'questions','flow','context','domain_knowledge'}.intersection(payload)
    assert len(payload['field_definitions'])==len(config.fields)
    assert payloads[0]['reasoning']['effort']==config.data['model']['reasoning_effort']

def test_provider_failure_has_no_hidden_second_call(system):
    config,_,engine=system
    count=[]
    def parse(**kwargs):
        count.append(1)
        raise RuntimeError('Provider unavailable')
    service=LLMService(config,SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with pytest.raises(RuntimeError):service.interpret('Answer',engine.view())
    assert len(count)==1

def test_unsupported_wrong_type_and_duplicate_updates_are_rejected(system):
    _,llm,engine=system
    updates=[update('integration_required','False'),update('business_owner','A'),update('business_owner','B'),update('not_a_field','X')]
    result=submit(llm,engine,updates)
    rejected=result['rejected']
    assert len(rejected)==4 and all(item['reason'] for item in rejected)
    assert engine.view().answers['business_owner'].status=='missing'

def test_renamed_field_and_priority_configuration_work(tmp_path,system):
    import shutil
    import yaml
    from configuration import Configuration
    from intake_engine import IntakeEngine
    from demand_service import MockJiraService
    config,llm,_=system
    for path in config.path.parent.glob('*.yaml'):shutil.copy(path,tmp_path/path.name)
    data=yaml.safe_load((tmp_path/'generic.yaml').read_text())
    fields=yaml.safe_load((tmp_path/'fields.yaml').read_text())
    questions=yaml.safe_load((tmp_path/'questions.yaml').read_text())
    next(f for f in fields['fields'] if f['id']=='requested_change')['id']='desired_change'
    for group in data['presentation']['groups']:
        group['fields']=['desired_change' if key=='requested_change' else key for key in group['fields']]
    for question in questions['questions']:
        question['context_fields']=['desired_change' if key=='requested_change' else key for key in question.get('context_fields',[])]
    first=questions['questions'][0]
    first['collect_fields']=['desired_change']
    first['id']='initial_request'
    first['ask_when']['field']='answers.desired_change'
    data['flow']['initial_question_id']='initial_request'
    next(q for q in questions['questions'] if q['id']=='primary_users')['priority']=99
    (tmp_path/'fields.yaml').write_text(yaml.safe_dump(fields,sort_keys=False))
    (tmp_path/'questions.yaml').write_text(yaml.safe_dump(questions,sort_keys=False))
    (tmp_path/'generic.yaml').write_text(yaml.safe_dump(data,sort_keys=False))
    changed=Configuration(tmp_path/'generic.yaml')
    llm.config=changed
    engine=IntakeEngine(changed,llm,MockJiraService(changed))
    submit(llm,engine,[update('desired_change','New feature')])
    assert engine.view().answers['desired_change'].value=='New feature'
    assert engine.view().current_question_id=='primary_users'

def test_bad_reference_rejected_at_startup(tmp_path,system):
    import shutil
    import yaml
    from configuration import Configuration
    config,_,_=system
    for path in config.path.parent.glob('*.yaml'):shutil.copy(path,tmp_path/path.name)
    data=yaml.safe_load((tmp_path/'generic.yaml').read_text())
    questions=yaml.safe_load((tmp_path/'questions.yaml').read_text())
    questions['questions'][0]['ask_when']['field']='answers.nonexistent'
    (tmp_path/'questions.yaml').write_text(yaml.safe_dump(questions,sort_keys=False))
    (tmp_path/'generic.yaml').write_text(yaml.safe_dump(data,sort_keys=False))
    with pytest.raises(ValueError,match='Unknown answer reference'):Configuration(tmp_path/'generic.yaml')
