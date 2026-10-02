from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.conftest import complete, submit, update
from models import Interpretation

APP=Path(__file__).resolve().parents[1]/'app.py'

def button(app,label):
    return next(button for button in app.button if button.label==label)

def test_confirm_generate_correct_and_new(system):
    config,llm,engine=system
    submit(llm,engine,complete())
    app=AppTest.from_file(str(APP))
    app.session_state['engine']=engine
    app.session_state['jira']=engine.jira
    app.run()
    assert not app.exception
    button(app,config.text('confirm')).click().run()
    button(app,config.text('generate')).click().run()
    assert not app.exception
    assert engine.view().issue['routing_destination']=='HR Operations'
    app.run()
    assert not any(b.label==config.text('generate') for b in app.button)
    assert len([c for c in llm.calls if c[0]=='generate'])==1
    llm.responses.append(Interpretation(updates=[update('business_owner','HR Lead')]))
    app.chat_input[0].set_value('Correction: HR Lead owns it').run()
    assert not app.exception
    assert engine.view().package is None and not engine.view().confirmed
    button(app,config.text('confirm')).click().run()
    button(app,config.text('generate')).click().run()
    assert engine.view().issue['key']=='DEMAND-1001'
    button(app,config.text('new')).click().run()
    assert not app.exception
    assert app.session_state['engine'].view().current_question_id=='requested_change'

def test_selection_advances_once_and_reruns_do_not_submit(system):
    config,llm,engine=system
    submit(llm,engine,[u for u in complete('Retail') if u.field_id!='application_context'])
    app=AppTest.from_file(str(APP))
    app.session_state['engine']=engine
    app.session_state['jira']=engine.jira
    app.run()
    app.selectbox[0].select('standalone').run()
    button(app,config.text('submit')).click().run()
    assert not app.exception
    version=engine.view().version
    app.run();app.run()
    assert engine.view().version==version
    assert len([m for m in engine.view().messages if m['role']=='user' and m['content']=='New standalone feature/application'])==1
    assert len([v for v in engine.view().visits if v.question_id=='application_context'])==1

def test_busy_screen_has_no_submission_widgets_and_recovers(system):
    _,_,engine=system
    app=AppTest.from_file(str(APP))
    app.session_state['engine']=engine
    app.session_state['jira']=engine.jira
    assert engine._lock.acquire(blocking=False)
    try:
        app.run()
        assert not app.exception
        assert not app.chat_input and not app.button
    finally:
        engine._lock.release()
    app.run()
    assert not app.exception
    assert app.chat_input and app.button
