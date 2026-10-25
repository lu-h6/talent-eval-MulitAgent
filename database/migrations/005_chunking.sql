DO $$ BEGIN
    CREATE TYPE chunking_status AS ENUM ('pending', 'running', 'succeeded', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE chunk_strategy AS ENUM ('recursive', 'markdown');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS chunking_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parse_job_id UUID NOT NULL REFERENCES parse_jobs(id),
    strategy chunk_strategy NOT NULL,
    status chunking_status NOT NULL DEFAULT 'pending',
    chunk_size INTEGER NOT NULL CHECK (chunk_size > 0),
    chunk_overlap INTEGER NOT NULL DEFAULT 0 CHECK (chunk_overlap >= 0),
    chunker_version VARCHAR(64) NOT NULL DEFAULT 'lesson-5-v1',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_chunking_runs_parse_job ON chunking_runs (parse_job_id, created_at DESC);

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunking_run_id UUID NOT NULL REFERENCES chunking_runs(id) ON DELETE CASCADE,
    document_version_id UUID NOT NULL REFERENCES document_versions(id),
    candidate_id VARCHAR(64) NOT NULL,
    document_type VARCHAR(64) NOT NULL,
    permission_scope VARCHAR(64) NOT NULL,
    stable_key VARCHAR(64) NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    chunk_level VARCHAR(16) NOT NULL DEFAULT 'child' CHECK (chunk_level IN ('parent', 'child')),
    content TEXT NOT NULL,
    element_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    parent_chunk_id UUID REFERENCES document_chunks(id),
    previous_chunk_id UUID REFERENCES document_chunks(id),
    next_chunk_id UUID REFERENCES document_chunks(id),
    page_start INTEGER,
    page_end INTEGER,
    timestamp_start DOUBLE PRECISION,
    timestamp_end DOUBLE PRECISION,
    markdown_start INTEGER,
    markdown_end INTEGER,
    source_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chunking_run_id, stable_key)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_version ON document_chunks (document_version_id, position);
CREATE INDEX IF NOT EXISTS idx_document_chunks_candidate ON document_chunks (candidate_id, document_type);

ALTER TABLE document_chunks
ADD COLUMN IF NOT EXISTS source_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS markdown_start INTEGER,
ADD COLUMN IF NOT EXISTS markdown_end INTEGER;

ALTER TABLE chunking_runs
DROP COLUMN IF EXISTS embedding_model,
DROP COLUMN IF EXISTS configuration;

COMMENT ON COLUMN document_chunks.source_locators IS
'原文件辅助定位集合，可保存页码与 bbox、幻灯片与 shape 或音频时间范围';

COMMENT ON COLUMN document_chunks.markdown_start IS
'Chunk 在归一化 Markdown 中的起始字符偏移';

COMMENT ON COLUMN document_chunks.markdown_end IS
'Chunk 在归一化 Markdown 中的结束字符偏移';

DROP TABLE IF EXISTS chunk_boundary_annotations;
DROP TABLE IF EXISTS chunk_evidence_questions;
