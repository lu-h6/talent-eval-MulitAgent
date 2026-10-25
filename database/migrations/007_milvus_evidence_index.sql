DO $$ BEGIN
    CREATE TYPE index_status AS ENUM ('pending', 'running', 'succeeded', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS evidence_index_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id UUID NOT NULL REFERENCES document_versions(id),
    status index_status NOT NULL DEFAULT 'pending',
    embedding_model VARCHAR(128) NOT NULL,
    collection_name VARCHAR(128) NOT NULL,
    indexed_count INTEGER NOT NULL DEFAULT 0 CHECK (indexed_count >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_index_jobs_version_created
ON evidence_index_jobs (document_version_id, created_at DESC);

COMMENT ON TABLE evidence_index_jobs IS 'PostgreSQL 与 Milvus 之间的证据索引任务及一致性状态';
