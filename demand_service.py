"""Pure output assembly plus an idempotent in-memory Jira adapter."""
from threading import Lock
import re
from conditions import matches

class DemandService:
    def __init__(self, config):
        self.config = config

    def route(self, state):
        routing = self.config.data['output']['routing']
        return next((rule['team'] for rule in routing['rules'] if matches(rule['when'], state)), routing['default_team'])

    def build(self, state, draft, route):
        output = self.config.data['output']
        expected = {section['id'] for section in output['generated_sections']}
        returned = set(draft['sections'])
        if expected != returned:
            raise ValueError(f"Section mismatch: missing={sorted(expected-returned)}, unexpected={sorted(returned-expected)}")
        title = draft['title'].strip()
        limits = output['title_limits']
        if not title or len(title) > limits['max_characters'] or len(title.split()) > limits['max_words']:
            raise ValueError(self.config.text('title_invalid'))
        sections, displaced = [], []
        for section in output['generated_sections']:
            text = draft['sections'][section['id']]
            if output['normalize_generated_linebreaks']:
                text = text.replace('\\r\\n', '\n').replace('\\n', '\n')
            if section['id'] == 'story':
                stories = re.split(r'\n\s*\n|\n(?=As (?:a|an|the) )', text.strip())
                text = '\n\n'.join(stories[:output['narrative_cleanup']['story_limit']])
            if section['id'] == 'acceptance':
                retained = []
                for line in text.splitlines():
                    if any(re.search(pattern, line, re.I) for pattern in output['narrative_cleanup']['refinement_patterns']):
                        displaced.append(re.sub(r'^\s*(?:[-*]|\d+[.)])\s*', '', line).strip())
                    else:
                        retained.append(line)
                text = '\n'.join(retained)

                text = re.sub(r'(?im)^(\s*(?:[-*]\s*|\d+[.)]\s*)?)Draft:\s*', r'\1', text)
                checks = []
                for key in output['acceptance_source_fields']:
                    answer = state.answers[key]
                    if answer.status == 'answered':
                        values = answer.value if isinstance(answer.value, list) else [str(answer.value)]
                        checks.extend(value for value in values if value not in text)
                if checks:
                    text += '\n\n' + output['acceptance_conditions_heading'] + ':\n' + '\n'.join('- ' + value for value in dict.fromkeys(checks))
            sections.append({'heading': section['heading'], 'id': section['id'], 'body': text})
        for section in output['copy_sections']:
            values = []
            for key in section['fields']:
                answer = state.answers[key]
                if answer.status == 'unconfirmed' and section.get('include_unconfirmed'):
                    values.append(self.config.field_map[key].label + ': ' + self.display(answer, key))
                if answer.status == 'answered':
                    entries = answer.value if isinstance(answer.value, list) else [str(answer.value)]
                    if section.get('label_values'):
                        entries = [self.config.field_map[key].label + ': ' + str(value) for value in entries]
                    values.extend(entries)
            if not values and section.get('omit_when_empty'):
                continue
            sections.append({'heading': section['heading'], 'id': section['id'],
                             'body': '\n'.join('- ' + value for value in dict.fromkeys(values)) or self.config.text('none')})
        followups = [self.config.text('followup', label=field.label, status=self.display(state.answers[field.id], field.id))
                     for field in self.config.fields if matches(field.applicable_when, state)
                     and (state.answers[field.id].status == 'unconfirmed'
                          or (field.required and state.answers[field.id].status == 'missing'))]
        followups.extend(displaced)
        followups.extend(item.clarification_need for item in state.pending_ambiguities)
        for rule in output.get('refinement_rules', []):
            if state.answers[rule['field']].status == 'answered' and matches(rule.get('when'), state):
                followups.append(rule['text'])
        for item in draft.get('refinement_items', []):
            sources = item['source_fields']
            if sources and all(key in state.answers and state.answers[key].status == 'answered' for key in sources):
                # Integration refinement is owned by the configured rule.
                if not any(key in output['refinement_owned_fields'] for key in sources):
                    followups.append(item['text'].strip())
        followups = list(dict.fromkeys(item for item in followups if item))
        assessment = self.validated_assessment(state, draft.get('classification_assessment', {}))
        classification = self.classification(state, assessment)
        if classification:
            sections.append({'heading': output['classification_heading'], 'id': 'classification',
                             'body': self.classification_table(classification)})
        order = output.get('section_order', [])
        sections.sort(key=lambda section: order.index(section['id']) if section['id'] in order else len(order))
        context = {'answers': {key: answer.model_dump() for key, answer in state.answers.items()}, 'confirmed': state.confirmed}
        return {'title': title, 'readiness': output['readiness_label'], 'routing_destination': route,
                'sections': sections, 'follow_up_items': followups,
                'confirmed_context': context,
                'classification': classification, 'classification_assessment': assessment, 'demand_id': state.demand_id, 'version': state.version}

    def display(self, answer, field_id=None):
        if answer.status != 'answered':
            return self.config.text('none' if answer.status == 'missing' else answer.status)
        if isinstance(answer.value, bool):
            return self.config.text('yes' if answer.value else 'no')
        labels = self.config.data['presentation']['value_labels'].get(field_id, {})
        if isinstance(answer.value, list):
            return '\n'.join('- ' + str(value) for value in answer.value)
        return labels.get(str(answer.value), str(answer.value))

    def review_rows(self, state, fields):
        return [(self.config.field_map[key].label, self.display(state.answers[key], key))
                for key in fields if key in state.answers
                and matches(self.config.field_map[key].applicable_when, state)
                and state.answers[key].status not in {'missing', 'not_applicable'}]

    def validated_assessment(self, state, assessment):
        result = {}
        for key in self.config.data['output']['assessment_fields']:
            value = assessment.get(key, 'Not confirmed')
            evidence = assessment.get(key + '_evidence', [])
            supported = bool(evidence) and isinstance(evidence, list) and all(
                isinstance(item, dict) and item.get('field_id') in state.answers
                and state.answers[item['field_id']].status == 'answered'
                and isinstance(item.get('quote'), str) and bool(item['quote'].strip())
                and item['quote'] in str(state.answers[item['field_id']].value)
                for item in evidence)
            if value not in self.config.data['output']['classification_taxonomy'][key] or not supported:
                value, evidence = 'Not confirmed', []
            result[key], result[key + '_evidence'] = value, evidence
        return result

    def classification(self, state, assessment):
        rows = []
        for spec in self.config.data['presentation']['classification_fields']:
            answer = state.answers[spec['field']]
            if answer.status == 'answered':
                rows.append({'attribute': spec['label'], 'value': self.display(answer, spec['field'])})
        assessment = self.validated_assessment(state, assessment)
        for key in self.config.data['output']['assessment_fields']:
            value = assessment[key]
            rows.append({'attribute': key.title(), 'value': value +
                         (' (initial estimate)' if key == 'complexity' and value != 'Not confirmed' else '')})
        return rows

    def classification_table(self, rows):
        def escape(value):
            return str(value).replace('|', '\\|').replace('\n', ' ')
        return '\n'.join(['| Attribute | Value |', '|---|---|'] +
                         ['| ' + escape(row['attribute']) + ' | ' + escape(row['value']) + ' |' for row in rows])

    def markdown(self, package):
        lines = ['# ' + package['title'], '', package['readiness'], '']
        for section in package['sections']:
            lines.extend(['## ' + section['heading'], '', section['body'], ''])
        lines.extend(['', '## ' + self.config.data['output']['routing']['heading'], '', package['routing_destination']])
        if package['follow_up_items']:
            lines.extend(['', '## ' + self.config.text('refinement_heading'), ''])
            lines.extend('- ' + item for item in package['follow_up_items'])
        return '\n'.join(lines) + '\n'

class MockJiraService:
    def __init__(self, config):
        self.settings = config.data['output']['jira']
        self.issues = {}
        self.lock = Lock()

    def create(self, package, description):
        identity = (package['demand_id'], package['version'])
        with self.lock:
            if identity not in self.issues:
                self.issues[identity] = {
                    'key': self.settings['project_key'] + '-' + str(self.settings['first_issue_number'] + len(self.issues)),
                    'summary': package['title'], 'description': description,
                    'routing_destination': package['routing_destination'],
                    'issue_type': self.settings['issue_type'], 'status': self.settings['status'], 'mock': True}
            return dict(self.issues[identity])
