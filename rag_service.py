"""Domain-filtered local vector retrieval; reference passages never update answers."""
import hashlib
import json
import math
import os
from collections import OrderedDict
from pathlib import Path
from tempfile import NamedTemporaryFile
import yaml
from dotenv import load_dotenv
from openai import OpenAI
from configuration import ROOT


class KnowledgeUnavailable(RuntimeError):
    pass


def normalized(vector):
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError('Invalid embedding vector')
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError('Empty embedding direction')
    return [value / norm for value in values]


class RAGService:
    def __init__(self, config, client=None):
        self.config = config
        self.settings = config.data['rag']
        self.client = client
        self.corpus_dir = (config.path.parent / self.settings['corpus_dir']).resolve()
        self.index_path = (config.path.parent / self.settings['index_path']).resolve()
        self._query_cache = OrderedDict()

    def chunks(self):
        chunks, document_ids = [], set()
        for path in sorted(self.corpus_dir.glob('*.yaml')):
            for document in yaml.safe_load(path.read_text(encoding='utf-8'))['documents']:
                if document['id'] in document_ids:
                    raise ValueError('Duplicate knowledge document ID')
                document_ids.add(document['id'])
                if document['domain'] not in self.settings['domains']:
                    raise ValueError('Unsupported knowledge domain')
                for section_index, section in enumerate(document['sections']):
                    words = section['text'].split()
                    pieces, part = [], []
                    for word in words:
                        if part and len(' '.join(part + [word])) > self.settings['max_chunk_characters']:
                            pieces.append(' '.join(part)); part = []
                        part.append(word)
                    if part:
                        pieces.append(' '.join(part))
                    for part_index, content in enumerate(pieces):
                        chunks.append({'chunk_id': f"{document['id']}:{section_index}:{part_index}",
                            'document_id': document['id'], 'domain': document['domain'],
                            'title': document['title'], 'version': document['version'],
                            'synthetic': document['synthetic'], 'process': document['process'],
                            'applications': document['applications'], 'source': path.name,
                            'heading': section['heading'], 'content': content})
        if not chunks:
            raise KnowledgeUnavailable('No knowledge documents')
        return chunks

    def signature(self, chunks):
        material = {'chunks': chunks, 'model': self.settings['embedding_model'],
                    'dimensions': self.settings['dimensions']}
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def embed(self, texts):
        if self.client is None:
            load_dotenv(ROOT / '.env')
            if not os.getenv('OPENAI_API_KEY'):
                raise KnowledgeUnavailable('OPENAI_API_KEY is required for embeddings')
            self.client = OpenAI(timeout=self.settings['timeout_seconds'], max_retries=self.settings['max_retries'])
        response = self.client.embeddings.create(model=self.settings['embedding_model'],
            input=texts, dimensions=self.settings['dimensions'], encoding_format='float')
        data = sorted(response.data, key=lambda item: item.index)
        if len(data) != len(texts):
            raise KnowledgeUnavailable('Incomplete embeddings response')
        vectors = [normalized(item.embedding) for item in data]
        if any(len(vector) != self.settings['dimensions'] for vector in vectors):
            raise KnowledgeUnavailable('Unexpected embedding dimensions')
        return vectors

    @staticmethod
    def embedding_text(chunk):
        return '\n'.join([chunk['domain'], chunk['process'], ', '.join(chunk['applications']),
                          chunk['heading'], chunk['content']])

    def build(self):
        chunks = self.chunks()
        fingerprint = self.signature(chunks)
        old = {}
        if self.index_path.exists():
            try:
                previous = json.loads(self.index_path.read_text(encoding='utf-8'))
                if previous['model'] == self.settings['embedding_model'] and previous['dimensions'] == self.settings['dimensions']:
                    if len(previous['chunks']) != len(previous['vectors']):
                        raise ValueError('Invalid previous index')
                    old = {self.embedding_text(chunk): normalized(vector) for chunk, vector in zip(previous['chunks'], previous['vectors'])}
                    if any(len(vector) != self.settings['dimensions'] for vector in old.values()):
                        raise ValueError('Invalid previous dimensions')
            except (ValueError, KeyError):
                old = {}
        texts = [self.embedding_text(chunk) for chunk in chunks]
        missing = list(dict.fromkeys(text for text in texts if text not in old))
        size = self.settings['batch_size']
        for start in range(0, len(missing), size):
            batch = missing[start:start + size]
            old.update(zip(batch, self.embed(batch)))
        index = {'fingerprint': fingerprint, 'model': self.settings['embedding_model'],
                 'dimensions': self.settings['dimensions'], 'chunks': chunks,
                 'vectors': [old[text] for text in texts]}
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.index_path.parent,
                                    suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump(index, file, ensure_ascii=False)
            temporary.replace(self.index_path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        self._query_cache.clear()
        return len(chunks)

    def query(self, state, question):
        parts = [question.base_question]
        for key in question.context_fields:
            answer = state.answers[key]
            if answer.status == 'answered':
                parts.append(str(answer.value))
        return '\n'.join(parts)[:self.settings['max_query_characters']]

    def search(self, state, question):
        if not self.settings['enabled'] or not question.rag_search:
            return []
        domain = state.answers[self.config.data['flow']['domain_field']]
        if domain.status != 'answered':
            return []
        allowed = [item for item in self.settings['domains']
                   if item.casefold() == str(domain.value).casefold()]
        if not allowed:
            return []
        try:
            chunks = self.chunks()
            index = json.loads(self.index_path.read_text(encoding='utf-8'))
            if index['fingerprint'] != self.signature(chunks):
                raise KnowledgeUnavailable('Knowledge index needs rebuilding')
            if len(index['vectors']) != len(index['chunks']):
                raise KnowledgeUnavailable('Invalid knowledge index')
            candidates = [(chunk, vector) for chunk, vector in zip(index['chunks'], index['vectors'])
                          if chunk['domain'] in allowed]
            if not candidates:
                return []
            query = self.query(state, question)
            key = (index['fingerprint'], query)
            if key in self._query_cache:
                vector = self._query_cache.pop(key)
            else:
                vector = self.embed([query])[0]
            self._query_cache[key] = vector
            while len(self._query_cache) > self.settings['query_cache_size']:
                self._query_cache.popitem(last=False)
            scored = []
            for chunk, candidate in candidates:
                candidate = normalized(candidate)
                if len(candidate) != len(vector):
                    raise KnowledgeUnavailable('Invalid index vector dimensions')
                score = sum(a * b for a, b in zip(vector, candidate))
                if score >= self.settings['minimum_similarity']:
                    scored.append((score, chunk))
            passages, counts, remaining = [], {}, self.settings['max_context_characters']
            for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True):
                document = chunk['document_id']
                if counts.get(document, 0) >= self.settings['max_chunks_per_document']:
                    continue
                content = chunk['content'][:min(remaining, self.settings['max_passage_characters'])]
                if not content:
                    break
                passages.append(dict(chunk, content=content))
                counts[document] = counts.get(document, 0) + 1
                remaining -= len(content)
                if len(passages) >= self.settings['top_k']:
                    break
            return passages
        except KnowledgeUnavailable:
            raise
        except Exception as error:
            raise KnowledgeUnavailable('Knowledge retrieval unavailable') from error
