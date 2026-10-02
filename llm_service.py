"""Structured model calls with bounded timeout and no hidden retries."""
import json
import os
from pydantic import ConfigDict, Field, create_model
from dotenv import load_dotenv
from openai import OpenAI
from configuration import ROOT
from models import Interpretation, QuestionWording

class LLMService:
    def __init__(self, config, client=None):
        load_dotenv(ROOT / '.env')
        self.config = config
        self.client = client
        setting = config.data['model']
        self.model = os.getenv(setting['environment_override']) or setting['name']

    def call(self, mode, payload, schema):
        setting = self.config.data['model']
        if self.client is None:
            if not os.getenv('OPENAI_API_KEY'):
                raise ValueError(self.config.text('api_key_missing'))
            self.client = OpenAI(timeout=setting['timeout_seconds'], max_retries=setting['max_retries'])
        response = self.client.responses.parse(model=self.model,
            input=[{'role': 'system', 'content': self.config.data['prompts'][mode]},
                   {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            text_format=schema, reasoning={'effort': setting['reasoning_effort']})
        if response.output_parsed is None:
            raise ValueError(self.config.text('parse_missing'))
        return response.output_parsed

    def facts(self, state):
        return {'answers': {key: {'value': answer.value, 'status': answer.status}
                            for key, answer in state.answers.items() if answer.status != 'missing'}}

    def reference_passages(self, passages):
        budget, result = self.config.data['llm_context']['max_knowledge_characters'], []
        for passage in passages:
            content = passage['content'][:budget]
            if not content:
                break
            result.append({key: passage[key] for key in ('chunk_id', 'domain', 'title', 'source', 'version', 'synthetic')}
                          | {'content': content})
            budget -= len(content)
        return result

    def interpret(self, message, state):
        question = self.config.question_map.get(state.current_question_id)
        return self.call('interpret', {
            'message': message,
            'current_question': {'id': question.id, 'text': state.current_question_text,
                                 'fields': list(dict.fromkeys(key for part in question.parts for key in part.fields))}
                                if question else None,
            'captured_answers': self.facts(state)['answers'],
            'field_definitions': [{'id': field.id, 'type': field.type, 'description': field.description,
                                  **({'allowed_values': field.allowed_values} if field.allowed_values else {})}
                                  for field in self.config.fields],
            'reference_passages': self.reference_passages(state.current_knowledge)
        }, Interpretation)

    def contextualize(self, question, state, passages):
        targets = list(dict.fromkeys(key for part in question.parts for key in part.fields))
        result = self.call('contextualize', {
            'base_question': question.base_question,
            'target_field_ids': targets,
            'missing_parts': [{'fields': part.fields, 'text': part.text} for part in question.parts],
            'choices': [option.label for option in question.input.options],
            'captured_facts': {key: state.answers[key].value for key in question.context_fields
                               if state.answers[key].status == 'answered'},
            'reference_passages': self.reference_passages(passages)
        }, QuestionWording)
        allowed = {passage['chunk_id'] for passage in passages}
        if (set(result.field_ids) != set(targets) or not result.question.strip()
                or not set(result.source_chunk_ids).issubset(allowed)):
            raise ValueError('Question wording does not match the selected question scope')
        if not result.source_chunk_ids:
            return question.base_question, []
        return result.question.strip(), list(dict.fromkeys(result.source_chunk_ids))

    def generate(self, state, routing):
        output = self.config.data['output']
        schema = generation_schema(output)
        facts = self.facts(state)
        result = self.call('generate', {'confirmed_context': facts, 'routing': routing,
                                      'generation_policy': {key: output[key] for key in
                                          ('title_limits', 'milestone_stages', 'refinement_owned_fields')}}, schema)
        return result.model_dump(by_alias=True)

def generation_schema(output):
    from typing import Literal
    evidence = create_model('AssessmentEvidence', __config__=ConfigDict(extra='forbid'),
        field_id=(str, ...), quote=(str, ...))
    assessment = create_model('ClassificationAssessment', __config__=ConfigDict(extra='forbid'),
        urgency=(Literal[tuple(output['classification_taxonomy']['urgency'])], ...),
        complexity=(Literal[tuple(output['classification_taxonomy']['complexity'])], ...),
        urgency_evidence=(list[evidence], ...), complexity_evidence=(list[evidence], ...))
    refinement = create_model('Refinement', __config__=ConfigDict(extra='forbid'),
        text=(str, ...), source_fields=(list[str], ...))
    fields = {'section_' + str(index): (str, Field(..., alias=section['id'], description=section['instruction']))
              for index, section in enumerate(output['generated_sections'])}
    sections = create_model('ConfiguredSections', __config__=ConfigDict(extra='forbid'), **fields)
    return create_model('ConfiguredDemand', __config__=ConfigDict(extra='forbid'),
        title=(str, Field(..., max_length=output['title_limits']['max_characters'])),
        sections=(sections, ...), classification_assessment=(assessment, ...),
        refinement_items=(list[refinement], ...))
