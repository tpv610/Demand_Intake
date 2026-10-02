import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from configuration import Configuration
from context_manager import ContextManager
from models import Update, Interpretation
from llm_service import LLMService
from rag_service import RAGService, KnowledgeUnavailable
from intake_engine import IntakeEngine
from demand_service import MockJiraService
from tests.conftest import StubLLM, complete, update, submit

class FakeEmbeddings:
    def __init__(self):
        self.requests = []
    def create(self, model, input, dimensions, encoding_format):
        self.requests.append(list(input))
        groups = [('leave','balance','workday'), ('invoice','supplier','payable'),
                  ('reconciliation','statement','ledger'), ('onboarding','joining','provisioning'),
                  ('expense','receipt','reimbursement'), ('task','comment','notification'),
                  ('budget','forecast','spending')]
        def vector(text):
            text=text.lower()
            return [float(sum(text.count(term) for term in group)) for group in groups] + [0.1]
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vector(text)) for i,text in enumerate(input)])

@pytest.fixture
def knowledge(tmp_path):
    source=Path(__file__).resolve().parents[1]
    shutil.copytree(source/'config',tmp_path/'config')
    shutil.copytree(source/'knowledge_base',tmp_path/'knowledge_base')
    path=tmp_path/'config/generic.yaml'
    data=yaml.safe_load(path.read_text());data['rag']['dimensions']=8
    data['rag']['minimum_similarity']=0.2
    path.write_text(yaml.safe_dump(data,sort_keys=False))
    config=Configuration(path)
    api=FakeEmbeddings()
    rag=RAGService(config,SimpleNamespace(embeddings=api))
    rag.build()
    return config,api,rag

def state_for(config,domain,request):
    manager=ContextManager(config);state=manager.initial()
    manager.apply(state,[Update(field_id='domain',value=domain,evidence=domain),
                        Update(field_id='requested_change',value=request,evidence=request)],'user')
    return state

def test_domain_filter_and_relevant_passages(knowledge):
    config,api,rag=knowledge
    question=config.question_map['integration_details']
    hr=rag.search(state_for(config,'HR','Check leave balances from Workday'),question)
    assert hr and all(p['domain']=='HR' for p in hr)
    assert hr[0]['document_id']=='hr_leave'
    finance=rag.search(state_for(config,'Finance','Match bank statement transactions with ledger entries for reconciliation'),question)
    assert finance and all(p['domain']=='Finance' for p in finance)
    assert finance[0]['document_id']=='finance_reconciliation'
    assert all(p['synthetic'] and p['chunk_id'] and p['source'] for p in finance)

def test_query_cache_and_no_search_for_disabled_questions(knowledge):
    config,api,rag=knowledge
    state=state_for(config,'HR','Read leave balances from Workday')
    question=config.question_map['integration_required']
    rag.search(state,question); count=len(api.requests)
    rag.search(state,question);assert len(api.requests)==count
    assert rag.search(state,config.question_map['owner'])==[]
    assert rag.search(state_for(config,'IT','Code completion'),question)==[]
    assert len(api.requests)==count

def test_index_rebuild_reuses_embeddings_and_detects_changed_documents(knowledge):
    config,api,rag=knowledge
    count=len(api.requests);rag.build();assert len(api.requests)==count
    path=rag.corpus_dir/'hr.yaml';data=yaml.safe_load(path.read_text())
    data['documents'][0]['sections'][0]['text']+=' New balance calculation reference.'
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(KnowledgeUnavailable):
        rag.search(state_for(config,'HR','Leave balance'),config.question_map['integration_details'])
    rag.build();assert len(api.requests)==count+1
    assert len(api.requests[-1])==1

def test_engine_retrieves_before_wording_and_does_not_apply_kb_facts(knowledge):
    config,api,rag=knowledge
    llm=StubLLM(config)
    engine=IntakeEngine(config,llm,MockJiraService(config),rag)
    updates=[u for u in complete('HR') if u.field_id!='application_context']
    updates=[update('requested_change','Check leave balances from Workday') if u.field_id=='requested_change' else u for u in updates]
    submit(llm,engine,updates)
    state=engine.view()
    assert state.current_knowledge and state.current_question_id=='application_context'
    assert state.current_question_text==config.question_map['application_context'].base_question
    assert [call[0] for call in llm.calls]==['interpret','contextualize']
    assert state.answers['existing_application'].status=='missing'
    assert state.messages[-1]['knowledge_sources'][0]['synthetic']
    assert not state.rag_notice

def test_missing_index_falls_back_and_retains_answer(knowledge):
    config,api,rag=knowledge;rag.index_path.unlink()
    llm=StubLLM(config);engine=IntakeEngine(config,llm,MockJiraService(config),rag)
    submit(llm,engine,[u for u in complete('HR') if u.field_id!='application_context'])
    assert engine.view().current_question_text==config.question_map['application_context'].base_question
    assert engine.view().answers['domain'].value=='HR'
    assert engine.view().rag_notice

def test_unresolved_domain_never_reads_corpus_or_embeds(knowledge):
    config,api,rag=knowledge
    state=ContextManager(config).initial()
    count=len(api.requests)
    rag.chunks=lambda: (_ for _ in ()).throw(AssertionError('Corpus accessed'))
    assert rag.search(state,config.question_map['integration_details'])==[]
    state.answers['domain'].status='unconfirmed'
    assert rag.search(state,config.question_map['integration_details'])==[]
    assert len(api.requests)==count

def test_question_passages_are_carried_into_next_answer_and_cleared_on_advance(knowledge):
    config,api,rag=knowledge
    llm=StubLLM(config)
    engine=IntakeEngine(config,llm,MockJiraService(config),rag)
    values=[u for u in complete('HR') if u.field_id!='application_context']
    values=[update('requested_change','Check leave balances') if u.field_id=='requested_change' else u for u in values]
    submit(llm,engine,values)
    assert engine.view().current_knowledge
    submit(llm,engine,[update('application_context','standalone')],'Standalone')
    before=llm.calls[-1][2]
    assert before.current_knowledge
    assert engine.view().current_question_id is None
    assert engine.view().current_knowledge==[]
    assert engine.view().answers['existing_application'].status=='not_applicable'


def test_engine_does_not_invoke_retrieval_for_unknown_domain(system):
    config,llm,engine=system
    calls=[]
    class ForbiddenRAG:
        def search(self,*args):
            calls.append(args)
            return []
    engine.rag=ForbiddenRAG()
    values=[u for u in complete() if u.field_id not in {'domain','application_context'}]
    values.append(update('domain',status='unconfirmed'))
    submit(llm,engine,values)
    submit(llm,engine,[update('domain',status='unconfirmed')])
    submit(llm,engine,[update('domain',status='unconfirmed')])
    state=engine.view()
    assert not calls
    assert state.current_question_id=='application_context'
    assert state.current_question_text==config.question_map['application_context'].base_question
    assert not state.current_knowledge and not state.rag_notice
    assert all(call[0]=='interpret' for call in llm.calls)
