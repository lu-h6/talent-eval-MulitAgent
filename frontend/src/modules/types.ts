export type Employee = {
  id: string;
  employee_no: string;
  name: string;
  gender?: string | null;
  age?: number | null;
  region?: string | null;
  current_position?: string | null;
  job_level?: string | null;
  years_of_experience?: number | null;
  department?: string | null;
  material_count: number;
};

export type KnowledgeBase = {
  id: string;
  name: string;
  description?: string | null;
  permission_scope: string;
  file_count: number;
};

export type DocumentSummary = {
  id: string;
  material_no?: string | null;
  candidate_id: string;
  employee_name: string;
  knowledge_base_id?: string | null;
  title: string;
  document_type: string;
  status: string;
  pipeline_status?: string | null;
  parse_status?: string | null;
  chunk_status?: string | null;
  index_status?: string | null;
  created_at: string;
};

export type CandidateProfile = {
  candidate_id: string;
  name: string;
  region?: string | null;
  current_position?: string | null;
  job_level?: string | null;
  years_of_experience?: number | null;
  department?: string | null;
};

export type FilterCondition = {
  field: string;
  operator: "eq" | "in" | "lt" | "lte" | "gt" | "gte";
  value: string | number | string[];
};

export type SemanticRequirement = {
  requirement_id: string;
  query: string;
  required: boolean;
};

export type Clarification = {
  expression: string;
  reason: string;
};

export type QueryPlan = {
  task_type: "find_talent" | "lookup_profile";
  filters: FilterCondition[];
  semantic_requirements: SemanticRequirement[];
  preferences: string[];
  clarifications: Clarification[];
};

export type SearchTrace = {
  requirement_id: string;
  strategy: string | null;
  queries: string[];
  chunk_count: number;
};

export type TalentChunk = {
  chunk_id: string;
  candidate_id: string;
  content: string;
  score: number;
  metadata: Record<string, unknown>;
  requirement_ids: string[];
};

export type EvidencePackCitation = {
  citation_id: string;
  chunk_id?: string;
  quote?: string;
  quote_start?: number;
  quote_end?: number;
  source_label?: string;
  document_title?: string;
  version_no?: number;
  heading_path?: string[];
  page_start?: number | null;
};

export type EvidencePackFactSource = {
  citation_id: string;
  chunk_id?: string;
  quote: string;
  quote_start: number;
  quote_end: number;
  source_label?: string;
};

export type EvidencePackFact = {
  event: string;
  period: string;
  claim: string;
  answer: "yes" | "no";
  sources: EvidencePackFactSource[];
};

export type EvidencePackRequirement = {
  requirement_id: string;
  query: string;
  status: "sufficient" | "partial" | "missing" | "conflicting";
  reason?: string;
  extraction_status: "not_run" | "succeeded" | "failed";
  facts: EvidencePackFact[];
  conflicts: number[][];
  missing_information: string[];
  citations: EvidencePackCitation[];
};

export type EvidencePack = {
  candidate_id: string;
  requirements: EvidencePackRequirement[];
};

export type TalentSearchResponse = {
  query_plan: QueryPlan;
  candidate_ids: string[];
  searches: SearchTrace[];
  chunks: TalentChunk[];
  evidence_packs?: EvidencePack[];
  evidence_status?: string;
};

export type EvidenceHit = {
  chunk_id: string;
  candidate_id: string;
  candidate_name: string;
  document_id?: string | null;
  document_title?: string | null;
  material_no?: string | null;
  document_type?: string | null;
  permission_scope?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  content: string;
  score: number;
  metadata: Record<string, unknown>;
};

export type CitationDetail = {
  citation_id: string;
  chunk_id: string;
  candidate_id: string;
  document_id: string;
  document_title: string;
  document_version_id: string;
  version_no: number;
  version_state: string;
  permission_scope: string;
  content: string;
  heading_path: string[];
  markdown_start: number | null;
  markdown_end: number | null;
  quote_start: number | null;
  quote_end: number | null;
  page_start: number | null;
  timestamp_start: number | null;
  parent_chunk_id: string | null;
};

export type JobDescription = {
  job_code: string;
  name: string;
  match_score: number;
  match_type: string;
  version: number;
  content: string;
};

export type CandidateProfilesResponse = {
  items: CandidateProfile[];
  missing: string[];
};
