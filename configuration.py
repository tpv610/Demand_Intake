from pathlib import Path
import yaml
from models import FieldSpec, Question, Context
from conditions import leaves

ROOT = Path(__file__).resolve().parent

class Configuration:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / 'config/generic.yaml')
        self.data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
        field_data = yaml.safe_load((self.path.parent / 'fields.yaml').read_text(encoding='utf-8'))
        question_data = yaml.safe_load((self.path.parent / 'questions.yaml').read_text(encoding='utf-8'))
        self.fields = [FieldSpec.model_validate(item) for item in field_data['fields']]
        self.questions = []
        for item in question_data['questions']:
            definition = dict(item)
            fields = definition.pop('collect_fields', None)
            if fields is not None:
                if definition.get('parts'):
                    raise ValueError('Use collect_fields or explicit parts, not both')
                definition['parts'] = [dict(id=definition['id'], fields=fields,
                                             text=definition['base_question'], ask_when=definition['ask_when'])]
            self.questions.append(Question.model_validate(definition))
        self.field_map = {item.id: item for item in self.fields}
        self.question_map = {item.id: item for item in self.questions}
        self.validate()

    def text(self, key, /, **values):
        return self.data['ui'][key].format(**values)

    def validate(self):
        def require(ok, message):
            if not ok:
                raise ValueError(message)
        require(len(self.fields) == len(self.field_map), 'Duplicate field IDs')
        require(len(self.questions) == len(self.question_map), 'Duplicate question IDs')
        flow = self.data['flow']
        phases = {item['id'] for item in flow['phases']}
        require(len(phases) == len(flow['phases']), 'Duplicate phases')
        require(flow['initial_question_id'] in self.question_map, 'Unknown initial question')
        require(flow['domain_field'] in self.field_map, 'Unknown domain field')
        rag = self.data['rag']
        require(all(isinstance(rag[key], int) and rag[key] > 0 for key in
                    ('dimensions', 'batch_size', 'top_k', 'max_passage_characters', 'max_context_characters',
                     'max_query_characters', 'max_chunk_characters', 'max_chunks_per_document', 'query_cache_size')),
                'Invalid retrieval limits')
        require(-1 <= rag['minimum_similarity'] <= 1, 'Invalid similarity threshold')
        require(bool(rag['domains']) and all(isinstance(domain, str) and domain.strip() for domain in rag['domains'])
                and len({domain.casefold() for domain in rag['domains']}) == len(rag['domains']), 'Invalid retrieval domains')
        limits = self.data['llm_context']
        require(all(isinstance(value, int) and value > 0 for value in limits.values()), 'Invalid LLM context limits')
        conditions = []
        for field in self.fields:
            conditions.extend([field.applicable_when, field.not_applicable_when])
        for question in self.questions:
            require(question.phase in phases, 'Unknown phase')
            conditions.append(question.ask_when)
            require(all(key in self.field_map for key in question.context_fields), 'Unknown question context field')
            require(bool(question.parts), 'Missing question parts')
            require(len({part.id for part in question.parts}) == len(question.parts), 'Duplicate question part IDs')
            for part in question.parts:
                require(bool(part.fields) and all(key in self.field_map for key in part.fields), 'Unknown question part field')
                conditions.append(part.ask_when)
            if question.input.control == 'select':
                require(question.input.target_field in self.field_map, 'Unknown dropdown target')
                require(bool(question.input.options), 'Missing dropdown options')
                require(len({o.id for o in question.input.options}) == len(question.input.options), 'Duplicate option IDs')
        for phase in flow['phases']:
            conditions.append(phase.get('resolve_when'))
            for key in ('attempt_counter', 'completion_flag'):
                if key in phase:
                    require(phase[key] in Context.model_fields, 'Unknown phase state')
        for rule in self.data['invalidation']:
            require(rule['field'] in self.field_map and all(key in self.field_map for key in rule['clear']), 'Unknown dependency')
        output = self.data['output']
        for section in output['copy_sections']:
            require(all(key in self.field_map for key in section['fields']), 'Unknown output field')
        ids = [section['id'] for section in output['generated_sections'] + output['copy_sections']]
        require(len(ids) == len(set(ids)), 'Duplicate section IDs')
        presentation = self.data['presentation']
        refs = list(output['acceptance_source_fields']) + list(output['refinement_owned_fields'])
        refs.extend(key for group in presentation['groups'] for key in group['fields'])
        refs.extend(item['field'] for item in presentation['classification_fields'])
        refs.extend(item['field'] for item in output.get('refinement_rules', []))
        require(all(key in self.field_map for key in refs), 'Unknown presentation/output field')
        require(all(key in self.field_map for key in presentation['value_labels']), 'Unknown display field')
        require(set(output['section_order']) == set(ids) | {'classification'}, 'Invalid section order')
        conditions.extend(rule.get('when') for rule in output.get('refinement_rules', []))
        conditions.extend(rule['when'] for rule in output['routing']['rules'])
        for condition in conditions:
            for leaf in leaves(condition):
                require(leaf['operator'] in {'missing', 'unresolved', 'answered', 'equals', 'greater_than_or_equal'}, 'Unknown operator')
                parts = leaf['field'].split('.')
                if parts[0] == 'answers':
                    require(len(parts) in {2, 3} and parts[1] in self.field_map, 'Unknown answer reference')
                    require(len(parts) == 2 or parts[2] in {'value', 'status', 'source', 'evidence'}, 'Invalid answer path')
                else:
                    require(len(parts) == 1 and parts[0] in Context.model_fields, 'Unknown state reference')
