from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from intake_engine import BusyError, StaleError
from tests.conftest import complete, submit, update

def test_snapshots_cannot_mutate_committed_state(system):
    _,_,engine=system
    copy=engine.view()
    copy.answers['requested_change'].value='Wrong'
    assert engine.view().answers['requested_change'].value is None

def test_initial_message_changes_only_supported_fields(system):
    _,llm,engine=system
    result=submit(llm,engine,[update('requested_change','Currency selection'),update('primary_users',['Shoppers'])])
    state=engine.view()
    for key in ('integration_required','integration_systems','integration_flow','existing_application'):
        assert state.answers[key].status=='missing'
    assert result['captured']==['requested_change','primary_users']

def test_api_failure_rolls_back_whole_turn(system):
    _,llm,engine=system
    before=engine.view().model_dump()
    llm.responses.append(RuntimeError('Network failure'))
    with pytest.raises(RuntimeError):engine.submit_text(0,'Requirement')
    assert engine.view().model_dump()==before

def test_stale_dropdown_replay_has_no_effect(system):
    _,llm,engine=system
    submit(llm,engine,[u for u in complete() if u.field_id!='integration_required'])
    view=engine.view()
    engine.submit_selection(view.version,'integration_required','no')
    committed=engine.view().model_dump()
    with pytest.raises(StaleError):engine.submit_selection(view.version,'integration_required','no')
    assert engine.view().model_dump()==committed
    assert sum(v.question_id=='integration_required' for v in engine.view().visits)==1

def test_overlapping_submissions_commit_once(system):
    _,llm,engine=system
    started,release=Event(),Event()
    original=llm.interpret
    def waiting(*args):
        started.set()
        assert release.wait(5)
        return original(*args)
    llm.interpret=waiting
    llm.responses.append(__import__('models').Interpretation(updates=complete()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(engine.submit_text,0,'Complete requirement')
        assert started.wait(3)
        assert engine.view().version==0
        with pytest.raises(BusyError):engine.submit_text(0,'Duplicate')
        release.set()
        first.result(timeout=5)
    assert engine.view().version==1
    assert len([message for message in engine.view().messages if message['role']=='user'])==1
    assert len(llm.calls)==1

def test_failed_generation_keeps_confirmation_and_can_retry(system):
    _,llm,engine=system
    submit(llm,engine,complete());engine.confirm(engine.view().version)
    before=engine.view().model_dump()
    original=llm.generate
    llm.generate=lambda *args:{'title':'Request','sections':{'wrong':'Text'}}
    with pytest.raises(ValueError,match='Section mismatch'):engine.generate(engine.view().version)
    assert engine.view().model_dump()==before
    llm.generate=original
    engine.generate(engine.view().version)
    assert engine.view().issue['mock']
