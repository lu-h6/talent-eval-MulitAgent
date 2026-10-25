"""Isolated course demo. Only newly authored synthetic materials can reach the model."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid5, NAMESPACE_URL

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api import routes as api
from app.db.database import get_db
from app.main import create_app
from app.db.models import Base, EmployeeProfile, FileObject, Document, DocumentVersion, ParseJob, ChunkingRun, DocumentChunk
from app.retrieval.evidence_pack import EvidenceExtraction, model_extractor
from app.integrations.milvus_store import EvidenceSearchResult
from app.integrations.model_provider import get_chat_model
from app.retrieval.query_plan import QueryPlan

SAMPLE_DIR = Path(__file__).resolve().parents[1] / 'samples' / 'lesson09'
QUERY = '在深圳且在 2025 年上半年担任星河项目总负责人的候选人'
REQUIREMENT = '在 2025 年 1 月至 6 月担任星河项目总负责人'


def make_demo(live_model=False):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    for candidate in ['C901', 'C902']:
        db.add(EmployeeProfile(employee_no=candidate, name=f'合成候选人{candidate}', region='深圳'))
    chunks = []
    for name in ['review', 'report', 'interview']:
        content = (SAMPLE_DIR / f'{name}.md').read_text()
        doc = Document(candidate_id='C901', tenant_id='course-demo', title=name, document_type='markdown')
        file = FileObject(bucket_name='isolated-fixture', object_key=name, original_name=name,
            mime_type='text/markdown', size_bytes=len(content.encode()), sha256='0' * 64)
        db.add_all([doc, file]); db.flush()
        version = DocumentVersion(document_id=doc.id, file_object_id=file.id, version_no=1)
        db.add(version); db.flush()
        job = ParseJob(document_version_id=version.id, parser_name='synthetic-fixture', parser_version='lesson09-v1')
        db.add(job); db.flush()
        run = ChunkingRun(parse_job_id=job.id, strategy='markdown', chunk_size=800)
        db.add(run); db.flush()
        chunk = DocumentChunk(id=uuid5(NAMESPACE_URL, 'lesson09/' + name), chunking_run_id=run.id,
            document_version_id=version.id, candidate_id='C901', document_type='markdown',
            permission_scope='hr_private', stable_key=name, position=0, content=content,
            markdown_start=0, markdown_end=len(content), heading_path=[content.splitlines()[0].lstrip('# ')])
        db.add(chunk); db.flush(); chunks.append(chunk)
    db.commit()
    live = get_chat_model(temperature=0) if live_model else None
    if live_model and live is None:
        raise RuntimeError('模型未配置，先设置项目 .env 中的 DASHSCOPE_API_KEY')
    if live is not None:
        live = live.model_copy(update={'request_timeout': 45, 'max_retries': 0})

    class DemoModel:
        def with_structured_output(self, schema):
            if schema is QueryPlan:
                return SimpleNamespace(invoke=lambda _: QueryPlan.model_validate({
                    'task_type': 'find_talent', 'filters': [{'field': 'region', 'operator': 'eq', 'value': '深圳'}],
                    'semantic_requirements': [{'requirement_id': 'S1', 'query': REQUIREMENT}]}))
            if schema is EvidenceExtraction and live is not None:
                structured = live.with_structured_output(schema)

                class VisibleSyntheticExtraction:
                    def invoke(self, messages):
                        result = structured.invoke(messages)
                        print('synthetic_model_output=' + result.model_dump_json())
                        return result

                return VisibleSyntheticExtraction()
            if schema is not EvidenceExtraction:
                raise ValueError('unsupported_demo_schema')
            refs = [{'chunk_id': str(row.id), 'quote': 'C901 在 2025 年 1 月至 6 月担任星河项目总负责人，负责排期和交付协调。'} for row in chunks[:2]]
            facts = [dict(event='星河项目', period='2025-01/2025-06', claim='担任项目总负责人',
                answer='yes', sources=refs),
                dict(event='星河项目', period='2025-01/2025-06', claim='担任项目总负责人', answer='no',
                    sources=[{'chunk_id': str(chunks[2].id), 'quote': '我在星河项目中只担任检索模块开发成员，未担任项目总负责人。'}])]
            return SimpleNamespace(invoke=lambda _: EvidenceExtraction(facts=facts,
                missing_information=['需要核查项目职责记录']))

    class DemoStore:
        def hybrid_search(self, *args, **kwargs):
            return [EvidenceSearchResult(chunk_id=str(c.id), candidate_id=c.candidate_id,
                content=c.content, score=0.03, metadata={'document_version_id': str(c.document_version_id)}) for c in chunks]
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    patches = [patch.object(api, 'get_chat_model', lambda **kw: DemoModel()),
        patch.object(api, 'get_embedding_model', lambda: SimpleNamespace(embed_query=lambda _: [0.1])),
        patch.object(api, 'get_reranker', lambda: SimpleNamespace(rerank=lambda **kw: [SimpleNamespace(index=i, score=0.5) for i in range(3)])),
        patch.object(api, 'get_evidence_store', lambda: DemoStore())]
    return app, db, chunks, patches, engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-model', action='store_true', help='仅把 samples/lesson09 合成材料发送到配置的模型 API')
    parser.add_argument('--serve', action='store_true', help='在 18089 端口启动隔离演示 API')
    args = parser.parse_args()
    app, db, chunks, patches, engine = make_demo(args.live_model)
    for item in patches: item.start()
    try:
        if args.serve:
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=18089)
            return
        client = TestClient(app)
        headers = {'X-Tenant-ID': 'course-demo', 'X-Permission-Scopes': 'hr_private'}
        response = client.post('/api/talent-search', headers=headers,
            json={'query': QUERY, 'include_evidence_pack': True, 'limit': 8})
        response.raise_for_status()
        body = response.json()
        mode = 'live-model' if args.live_model else 'fixture-model'
        target = Path('artifacts') / f'lesson09-{mode}.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(body, ensure_ascii=False, indent=2))
        summary = {'mode': mode, 'candidate_ids': body['candidate_ids'], 'chunk_count': len(body['chunks']),
            'requirements': [{'candidate_id': p['candidate_id'], 'status': r['status'],
                'extraction_status': r['extraction_status'], 'facts': len(r['facts']), 'citations': len(r['citations'])}
                for p in body['evidence_packs'] for r in p['requirements']]}
        extraction_states = [r['extraction_status'] for p in body['evidence_packs'] for r in p['requirements']
            if r['extraction_status'] != 'not_run']
        summary['model_result'] = 'safe_fallback' if 'failed' in extraction_states else 'extracted'
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f'response_saved={target}')
        if not args.live_model:
            assert 'failed' not in extraction_states
    finally:
        for item in reversed(patches): item.stop()
        db.close(); engine.dispose()


if __name__ == '__main__': main()
