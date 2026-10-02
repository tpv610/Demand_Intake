"""Build or refresh the synthetic knowledge vector index using your API key."""
from configuration import Configuration
from rag_service import RAGService

if __name__ == '__main__':
    try:
        count = RAGService(Configuration()).build()
        print(f'Knowledge index ready: {count} chunks. Existing unchanged embeddings reused.')
    except Exception as error:
        raise SystemExit(f'Index build failed ({type(error).__name__}). Check API access, .env and corpus configuration.')
