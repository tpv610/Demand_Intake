"""Contracts for configuration, business state and one model interpretation."""
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field

Value = str | list[str] | bool | int | float | None
Status = Literal['missing', 'answered', 'unconfirmed', 'not_applicable']

class Answer(BaseModel):
    value: Value = None
    status: Status = 'missing'
    evidence: str = ''
    source: str = ''

class FieldSpec(BaseModel):
    id: str
    label: str
    type: Literal['text', 'list', 'boolean', 'integer', 'number', 'enum']
    description: str
    required: bool = False
    allowed_values: list[str] = Field(default_factory=list)
    applicable_when: dict | None = None
    not_applicable_when: dict | None = None

class Option(BaseModel):
    id: str
    label: str
    kind: Literal['value', 'text'] = 'value'
    value: Value = None
    status: Status = 'answered'

class InputSpec(BaseModel):
    control: Literal['text', 'select'] = 'text'
    target_field: str | None = None
    options: list[Option] = Field(default_factory=list)

class QuestionPart(BaseModel):
    id: str
    fields: list[str]
    text: str
    ask_when: dict

class Question(BaseModel):
    id: str
    phase: str
    priority: int
    base_question: str
    rag_search: bool = False
    context_fields: list[str] = Field(default_factory=list)
    ask_when: dict
    max_answer_attempts: int | None = None
    input: InputSpec = Field(default_factory=InputSpec)
    parts: list[QuestionPart] = Field(default_factory=list)

class Update(BaseModel):
    field_id: str
    value: Value = None
    status: Status = 'answered'
    evidence: str
    supported: bool = True

class Ambiguity(BaseModel):
    field_ids: list[str]
    evidence: str
    clarification_need: str

class Interpretation(BaseModel):
    updates: list[Update] = Field(default_factory=list)
    answers_current_question: bool = True
    ambiguities: list[Ambiguity] = Field(default_factory=list)

class Visit(BaseModel):
    question_id: str
    phase: str
    opened_version: int
    closed_version: int | None = None
    failures: int = 0

class Context(BaseModel):
    demand_id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 0
    answers: dict[str, Answer] = Field(default_factory=dict)
    field_versions: dict[str, int] = Field(default_factory=dict)
    current_question_id: str | None = None
    current_question_text: str | None = None
    phase: str = ''
    domain_clarification_attempts: int = 0
    domain_discovery_complete: bool = False
    visits: list[Visit] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)
    rag_notice: str = ''
    current_knowledge: list[dict] = Field(default_factory=list)
    pending_ambiguities: list[Ambiguity] = Field(default_factory=list)
    confirmed: bool = False
    package: dict | None = None
    issue: dict | None = None


class QuestionWording(BaseModel):
    question: str
    field_ids: list[str]
    source_chunk_ids: list[str] = Field(default_factory=list)
