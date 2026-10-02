"""Basic Streamlit UI. It reads snapshots and submits versioned commands only."""
import json
import os
import re
import streamlit as st
from configuration import Configuration
from llm_service import LLMService
from intake_engine import IntakeEngine, BusyError, StaleError
from demand_service import MockJiraService

def error_reason(error):
    """Explain failures without recording logs or exposing credentials."""
    name = type(error).__name__
    hints = {
        'AuthenticationError': 'The API key was rejected. Check OPENAI_API_KEY in .env.',
        'PermissionDeniedError': 'The API account does not have permission for this request.',
        'APITimeoutError': 'The model request timed out. Retry your answer.',
        'APIConnectionError': 'The API could not be reached. Check network or proxy settings.',
        'RateLimitError': 'The API rejected the request due to a rate or usage limit.',
    }
    if name in hints:
        return hints[name]
    detail = str(error)
    secret = os.getenv('OPENAI_API_KEY')
    if secret:
        detail = detail.replace(secret, '[redacted]')
    detail = re.sub(r'sk-[A-Za-z0-9_-]+', '[redacted]', detail)
    detail = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [redacted]', detail)
    return name + ': ' + detail[:800]

config = Configuration()
st.set_page_config(page_title=config.text('title'))
if 'jira' not in st.session_state:
    st.session_state.jira = MockJiraService(config)
if 'engine' not in st.session_state:
    st.session_state.engine = IntakeEngine(config, LLMService(config), st.session_state.jira)
engine = st.session_state.engine
state = engine.view()
st.title(config.text('title'))
st.caption(config.text('caption'))

if engine.busy:
    st.info(config.text('busy'))
    @st.fragment(run_every=1.0)
    def wait_for_completion():
        if not engine.busy:
            st.rerun(scope='app')
    wait_for_completion()
    st.stop()

if st.button(config.text('new'), key='new'):
    if not engine.busy:
        st.session_state.engine = IntakeEngine(config, LLMService(config), st.session_state.jira)
        st.rerun()

question = config.question_map.get(state.current_question_id)
def show_conversation():
    for message in state.messages:
        with st.chat_message(message['role']):
            st.write(message['content'])
if question is None:
    with st.expander(config.text('conversation_title')):
        show_conversation()
else:
    show_conversation()

if state.rag_notice:
    st.info(state.rag_notice)
if question and question.input.control == 'select':
    options = {option.id: option for option in question.input.options}
    with st.form('choice_' + state.demand_id + '_' + str(state.version)):
        selected = st.selectbox(config.text('choice_label'), list(options), index=None,
                               placeholder=config.text('choice_placeholder'),
                               format_func=lambda key: options[key].label)
        submitted = st.form_submit_button(config.text('submit'))
    if submitted:
        if selected is None:
            st.info(config.text('select_hint'))
        elif options[selected].kind == 'text':
            st.info(config.text('other_hint'))
        else:
            try:
                with st.spinner(config.text('processing')):
                    engine.submit_selection(state.version, question.id, selected)
                st.rerun()
            except (BusyError, StaleError):
                st.rerun()
            except Exception as error:
                st.error(config.text('error') + '\n\n' + error_reason(error))

if question is None and not state.package:
    st.info(config.text('ready'))
    st.subheader(config.text('summary_title'))
    for group in config.data['presentation']['groups']:
        rows = engine.output.review_rows(state, group['fields'])
        if rows:
            st.markdown('### ' + group['heading'])
            for label, value in rows:
                st.markdown('**' + label + '**')
                st.markdown(value)
    blocking = engine.planner.blocking(state)
    if blocking:
        st.warning(config.text('blocked', fields=', '.join(config.field_map[key].label for key in blocking)))
    elif not state.confirmed:
        if st.button(config.text('confirm'), key='confirm_' + str(state.version)):
            try:
                engine.confirm(state.version)
                st.rerun()
            except (BusyError, StaleError):
                st.rerun()
    else:
        st.success(config.text('confirmed'))

if text := st.chat_input(config.text('chat_placeholder'), key='chat_' + state.demand_id + '_' + str(state.version)):
    try:
        with st.spinner(config.text('processing')):
            engine.submit_text(state.version, text)
        st.rerun()
    except (BusyError, StaleError):
        st.rerun()
    except Exception as error:
        st.error(config.text('error') + '\n\n' + error_reason(error))

if state.confirmed and question is None and not state.package:
    if st.button(config.text('generate'), key='generate_' + str(state.version), type='primary'):
        try:
            with st.spinner(config.text('generate_spinner')):
                engine.generate(state.version)
            st.rerun()
        except (BusyError, StaleError):
            st.rerun()
        except Exception as error:
            st.error(config.text('error') + '\n\n' + error_reason(error))

if state.package:
    markdown = engine.output.markdown(state.package)
    st.success(config.text('jira_created', key=state.issue['key']))
    st.markdown(markdown)
    filename = config.data['output']['filename']
    payload = json.dumps({'demand': state.package, 'jira': state.issue}, indent=2, ensure_ascii=False)
    st.download_button(config.text('download_md'), markdown, file_name=filename + '.md', mime='text/markdown')
    st.download_button(config.text('download_json'), payload, file_name=filename + '.json', mime='application/json')
    with st.expander(config.text('json_title')):
        st.json({'demand': state.package, 'jira': state.issue})

