from copy import deepcopy
import importlib.util
import pytest


def module():
    assert importlib.util.find_spec('app.retrieval.evidence_pack'), '证据整理模块尚未实现'
    from app.retrieval import evidence_pack
    return evidence_pack


def source(cid='a', candidate='C001'):
    return {'chunk_id': cid, 'candidate_id': candidate, 'content': '负责星河项目。',
            'citation_id': cid, 'document_version_id': 'v1', 'requirement_ids': ['S1']}


def extraction():
    return {'facts': [{'event': '星河项目', 'period': '2025',
                       'claim': '担任项目负责人', 'answer': 'yes',
                       'sources': [{'chunk_id': 'a', 'quote': '负责星河项目'}]}],
            'fully_supported': True, 'missing_information': []}


def build(data=None, sources=None, candidates=None):
    return module().build_evidence_packs(
        candidate_ids=candidates or ['C001'],
        requirements=[{'requirement_id': 'S1', 'query': '负责过星河项目'}],
        sources=sources if sources is not None else [source()],
        extract=lambda requirement, rows: data if data is not None else extraction())


def test_duplicate_chunk_is_not_counted_twice_and_missing_candidate_is_preserved():
    packs = build(sources=[source(), source()], candidates=['C001', 'C002'])
    assert packs[0]['schema_version'] == '2.0'
    assert len(packs[0]['requirements'][0]['citations']) == 1
    assert packs[0]['requirements'][0]['status'] == 'sufficient'
    assert packs[1]['requirements'][0]['status'] == 'missing'


def test_same_fact_merges_sources_without_dropping_provenance():
    data = extraction()
    second = deepcopy(data['facts'][0])
    second['sources'][0]['chunk_id'] = 'b'
    data['facts'].append(second)
    requirement = build(data, [source(), source('b')])[0]['requirements'][0]
    assert len(requirement['facts']) == 1
    assert len(requirement['facts'][0]['sources']) == 2


def test_verified_quote_includes_offsets_from_chunk_content():
    reference = build()[0]['requirements'][0]['facts'][0]['sources'][0]
    assert reference['quote'] == '负责星河项目'
    assert reference['quote_start'] == 0
    assert reference['quote_end'] == 6


def test_quote_with_outer_whitespace_is_trimmed_before_exact_source_validation():
    data = extraction()
    data['facts'][0]['sources'][0]['quote'] = '  负责星河项目  '

    reference = build(data)[0]['requirements'][0]['facts'][0]['sources'][0]

    assert reference['quote'] == '负责星河项目'
    assert reference['quote_start'] == 0
    assert reference['quote_end'] == 6


def test_unverifiable_quote_logs_safe_diagnostic_context(caplog):
    data = extraction()
    data['facts'][0]['sources'][0]['quote'] = '主持千人团队'

    with caplog.at_level('ERROR', logger='app.retrieval.evidence_pack'):
        result = build(data)[0]['requirements'][0]

    assert result['extraction_status'] == 'failed'
    message = caplog.messages[-1]
    assert 'event=evidence_extraction_failed' in message
    assert 'function=_validate_fact_sources' in message
    assert 'stage=source_validation' in message
    assert 'error_code=quote_not_found' in message
    assert 'candidate_id=C001' in message
    assert 'requirement_id=S1' in message
    assert 'chunk_id=a' in message
    assert 'source_count=1' in message
    assert '负责星河项目。' not in message
    assert '主持千人团队' not in message


@pytest.mark.parametrize('bad', ['unknown_id', 'fabricated_quote'])
def test_unverifiable_model_output_falls_back_to_raw_citations(bad):
    data = extraction()
    data['facts'][0]['sources'][0].update(
        {'chunk_id': 'other-person'} if bad == 'unknown_id' else {'quote': '主持千人团队'})
    result = build(data)[0]['requirements'][0]
    assert result['extraction_status'] == 'failed'
    assert result['facts'] == []
    assert len(result['citations']) == 1
    assert result['status'] == 'partial'


def test_yes_and_no_answers_in_same_scope_form_conflict():
    data = extraction()
    opposite = deepcopy(data['facts'][0])
    opposite.update(answer='no')
    opposite['sources'] = [{'chunk_id': 'b', 'quote': '仅参与星河项目'}]
    data['facts'].append(opposite)
    rows = [source(), dict(source('b'), content='仅参与星河项目。')]
    requirement = build(data, rows)[0]['requirements'][0]
    assert requirement['status'] == 'conflicting'
    assert requirement['reason'] == 'evidence_review'
    assert requirement['conflicts'] == [[0, 1]]


def test_yes_and_no_answers_in_different_periods_are_not_a_conflict():
    data = extraction()
    opposite = deepcopy(data['facts'][0])
    opposite.update(period='2024', answer='no')
    opposite['sources'] = [{'chunk_id': 'b', 'quote': '仅参与星河项目'}]
    data['facts'].append(opposite)
    data['fully_supported'] = False
    rows = [source(), dict(source('b'), content='仅参与星河项目。')]
    requirement = build(data, rows)[0]['requirements'][0]
    assert requirement['status'] == 'partial'
    assert requirement['conflicts'] == []


def test_noncanonical_period_falls_back_instead_of_entering_conflict_check():
    data = extraction()
    data['facts'][0]['period'] = '2025 年 1 月至 2025 年 6 月'
    result = build(data)[0]['requirements'][0]
    assert result['status'] == 'partial'
    assert result['extraction_status'] == 'failed'


def test_model_failure_is_not_material_absence():
    def fail(*args):
        raise TimeoutError('private details')

    packs = module().build_evidence_packs(candidate_ids=['C001'], requirements=[
        {'requirement_id': 'S1', 'query': '负责过星河项目'}], sources=[source()], extract=fail)
    result = packs[0]['requirements'][0]
    assert result['status'] == 'partial'
    assert result['extraction_status'] == 'failed'
    assert 'private details' not in str(result)


def test_irrelevant_evidence_is_distinguished_from_no_hits():
    result = build({'facts': [], 'fully_supported': False,
                    'missing_information': ['没有项目职责信息']})[0]['requirements'][0]
    assert result['status'] == 'missing'
    assert result['reason'] == 'no_relevant_evidence'


def test_blank_missing_information_is_rejected():
    data = extraction()
    data['missing_information'] = ['']
    result = build(data)[0]['requirements'][0]
    assert result['status'] == 'partial'
    assert result['extraction_status'] == 'failed'


def test_model_extractor_uses_json_mode_and_sends_the_output_schema():
    calls = {}

    class StructuredModel:
        def invoke(self, messages):
            calls['messages'] = messages
            return module().EvidenceExtraction(
                facts=[],
                fully_supported=False,
                missing_information=['材料未说明职责'],
            )

    class Model:
        def with_structured_output(self, schema, **kwargs):
            calls['schema'] = schema
            calls['kwargs'] = kwargs
            return StructuredModel()

    extract = module().model_extractor(Model())
    result = extract(
        {'requirement_id': 'S1', 'query': '负责过星河项目'},
        [source()],
    )

    payload = calls['messages'][1][1]
    assert result.missing_information == ['材料未说明职责']
    assert calls['schema'] is module().EvidenceExtraction
    assert calls['kwargs'] == {'method': 'json_mode'}
    assert 'output_schema' in payload


def test_model_extractor_uses_short_source_refs_and_restores_real_chunk_ids():
    calls = {}

    class StructuredModel:
        def invoke(self, messages):
            calls['payload'] = __import__('json').loads(messages[1][1])
            return module().EvidenceExtraction.model_validate({
                'facts': [{
                    'event': '星河项目',
                    'period': '2025',
                    'claim': '担任项目负责人',
                    'answer': 'yes',
                    'sources': [{
                        'chunk_id': 'source_1',
                        'quote': '负责星河项目',
                    }],
                }],
                'fully_supported': True,
                'missing_information': [],
            })

    class Model:
        def with_structured_output(self, schema, **kwargs):
            del schema, kwargs
            return StructuredModel()

    result = module().model_extractor(Model())(
        {'requirement_id': 'S1', 'query': '负责过星河项目'},
        [source('a-real-uuid')],
    )

    assert calls['payload']['sources'][0]['chunk_id'] == 'source_1'
    assert result.facts[0].sources[0].chunk_id == 'a-real-uuid'



def _fact(event='星河项目', period='2025', claim='担任项目负责人',
          answer='yes', quotes=('负责星河项目',),
          chunk_ids=('a',)):
    ep = module()
    return ep.ExtractedFact(
        event=event, period=period, claim=claim, answer=answer,
        sources=[ep.FactSource(chunk_id=cid, quote=q)
                 for q, cid in zip(quotes, chunk_ids)],
    )

def _rows():
    return [
        {'chunk_id': 'a', 'citation_id': 'cite_a', 'content': '负责星河项目。',
         'document_title': '述职报告', 'source_label': 'doc-A'},
        {'chunk_id': 'b', 'citation_id': 'cite_b', 'content': '负责星河项目。',
         'document_title': '绩效记录', 'source_label': 'doc-B'},
    ]

def test_validate_fact_sources_valid_and_dedup():
    ep = module()
    facts = [
        _fact(quotes=('负责星河项目', '负责星河项目'),
              chunk_ids=('a', 'a')),   # 同一来源重复引用 -> 去重
        _fact(event='智云平台', claim='统筹整体进度',
              quotes=('负责星河项目',), chunk_ids=('b',)),
    ]

    validated = ep._validate_fact_sources(facts, _rows())

    assert validated[0]['event'] == '星河项目'
    assert validated[0]['answer'] == 'yes'
    assert len(validated[0]['sources']) == 1  # 重复来源被去重
    ref = validated[0]['sources'][0]
    assert ref['citation_id'] == 'cite_a'
    assert ref['chunk_id'] == 'a'
    assert ref['quote'] == '负责星河项目'
    assert ref['quote_start'] == 0
    assert ref['quote_end'] == 6
    assert ref['source_label'] == '述职报告'  # 优先 document_title

def test_validate_fact_sources_unknown_chunk_id():
    """
    测试校验未知分块。
    """
    ep = module()
    facts = [_fact(chunk_ids=('ghost',))]

    with pytest.raises(ep.EvidenceSourceValidationError) as exc:
        ep._validate_fact_sources(facts, _rows())
    assert exc.value.code == 'unknown_chunk_id'
    assert exc.value.chunk_id == 'ghost'
    assert exc.value.fact_index == 0
    assert exc.value.source_index == 0
    assert exc.value.source_length is None

def test_validate_fact_sources_blank_quote():
    """
    测试校验空引用。

    """
    ep = module()
    facts = [_fact(quotes=('   ',), chunk_ids=('a',))]

    with pytest.raises(ep.EvidenceSourceValidationError) as exc:
        ep._validate_fact_sources(facts, _rows())
    assert exc.value.code == 'blank_quote'
    assert exc.value.quote_length == 0
    assert exc.value.source_length == 7  # '负责星河项目。' 的长度

def test_validate_fact_sources_quote_not_found():
    """
    测试校验引用不存在的情况。

    """
    ep = module()
    facts = [_fact(quotes=('主持千人团队',), chunk_ids=('a',))]

    with pytest.raises(ep.EvidenceSourceValidationError) as exc:
        ep._validate_fact_sources(facts, _rows())
    assert exc.value.code == 'quote_not_found'
    assert exc.value.chunk_id == 'a'
    assert exc.value.quote_length == 6      # '主持千人团队' 的长度
    assert exc.value.source_length == 7     # '负责星河项目。' 的长度


# _merge_duplicate_facts 接收的是 _validate_fact_sources 的输出（dict 列表，而非 Pydantic 对象），
# 因此这里直接用 dict 构造输入，更贴近真实链路。

def _merged_fact(event='星河项目', period='2025', claim='担任项目负责人',
                 answer='yes', sources=()):
    return {'event': event, 'period': period, 'claim': claim,
            'answer': answer, 'sources': list(sources)}


def test_merge_duplicate_facts_merges_by_key_and_accumulates_sources():
    ep = module()
    # 相同的 event/period/claim/answer，但来源不同
    first = _merged_fact(sources=[
        {'chunk_id': 'a', 'quote': '负责星河项目', 'quote_start': 0, 'quote_end': 6,
         'citation_id': 'cite_a', 'source_label': '述职报告'},
    ])
    second = _merged_fact(sources=[
        {'chunk_id': 'b', 'quote': '负责星河项目', 'quote_start': 0, 'quote_end': 6,
         'citation_id': 'cite_b', 'source_label': '绩效记录'},
    ])

    merged = ep._merge_duplicate_facts([first, second])

    assert len(merged) == 1                     # 相同 key 合并为 1 条
    assert merged[0]['event'] == '星河项目'
    assert len(merged[0]['sources']) == 2       # 全部来源被保留
    assert {s['citation_id'] for s in merged[0]['sources']} == {'cite_a', 'cite_b'}


def test_merge_duplicate_facts_dedup_same_source_within_merge():
    ep = module()
    ref_a = {'chunk_id': 'a', 'quote': '负责星河项目', 'quote_start': 0, 'quote_end': 6,
             'citation_id': 'cite_a', 'source_label': '述职报告'}
    first = _merged_fact(sources=[ref_a])
    second = _merged_fact(sources=[ref_a])      # 已存在的来源不再重复加入

    merged = ep._merge_duplicate_facts([first, second])

    assert len(merged) == 1
    assert len(merged[0]['sources']) == 1


def test_merge_duplicate_facts_distinct_keys_remain_separate():
    ep = module()
    a = _merged_fact(event='星河项目', claim='担任项目负责人', answer='yes')
    b = _merged_fact(event='星河项目', claim='担任项目负责人', answer='no')   # 答案不同 -> 不合并
    c = _merged_fact(event='星河项目', claim='统筹整体进度', answer='yes')   # claim 不同 -> 不合并

    merged = ep._merge_duplicate_facts([a, b, c])

    assert len(merged) == 3
    assert [f['answer'] for f in merged] == ['yes', 'no', 'yes']


# _find_conflicts 只用 event/period/claim/answer 四个键做两两比较，直接复用 _merged_fact 构造 dict 输入。

def test_find_conflicts_yes_no_in_same_scope():
    ep = module()
    facts = [
        _merged_fact(answer='yes'),
        _merged_fact(answer='no'),   # 相同 event/period/claim，答案相反
    ]

    conflicts = ep._find_conflicts(facts)

    assert conflicts == [[0, 1]]


def test_find_conflicts_none_for_same_answer_or_no_overlap():
    ep = module()
    # 索引 0/1：answer 相同；索引 2：event 不同 -> 都不冲突
    facts = [
        _merged_fact(answer='yes'),
        _merged_fact(answer='yes'),
        _merged_fact(event='智云平台', claim='统筹整体进度', answer='no'),
    ]

    conflicts = ep._find_conflicts(facts)

    assert conflicts == []


def test_find_conflicts_ignores_unknown_scope_value():
    ep = module()
    # period 或 claim 为 'unknown' 时，不判定冲突（尽管答案相反）
    facts = [
        _merged_fact(period='unknown', answer='yes'),
        _merged_fact(period='unknown', answer='no'),
    ]

    conflicts = ep._find_conflicts(facts)

    assert conflicts == []


def test_find_conflicts_multiple_pairs_return_in_order():
    ep = module()
    # 0-1 冲突，0-2 冲突，1-2 不冲突（answer 相同）
    facts = [
        _merged_fact(claim='担任项目负责人', answer='yes'),
        _merged_fact(claim='担任项目负责人', answer='no'),
        _merged_fact(claim='统筹整体进度', answer='yes'),
    ]

    conflicts = ep._find_conflicts(facts)

    # 这里只有 0-1 一对（0-2 的 claim 不同）
    assert conflicts == [[0, 1]]


# ---- build_evidence_packs 端到端测试 ----


def _pack_rows():
    return [
        {'chunk_id': 'ca1', 'candidate_id': 'C001', 'requirement_ids': ['S1'],
         'content': '张伟担任星河项目总负责人。',
         'citation_id': 'cite_1', 'document_title': '述职报告'},
        {'chunk_id': 'ca2', 'candidate_id': 'C001', 'requirement_ids': ['S1'],
         'content': '仅参与前期方案评审。',
         'citation_id': 'cite_2', 'document_title': '项目周报'},
        # C001 命中 S2，但替身对 S2 返回空事实 -> 走 no_relevant_evidence
        {'chunk_id': 'ca3', 'candidate_id': 'C001', 'requirement_ids': ['S1', 'S2'],
         'content': '参与星河项目汇报。',
         'citation_id': 'cite_3', 'document_title': '项目周报'},
        {'chunk_id': 'cb1', 'candidate_id': 'C002', 'requirement_ids': ['S1'],
         'content': '李四参与星河项目。',
         'citation_id': 'cite_4', 'document_title': '绩效记录'},
    ]


def _pack_extract(requirement, rows):
    """按 requirement_id 返回不同结果的测试替身。"""
    ep = module()
    if requirement['requirement_id'] == 'S1' and rows[0]['candidate_id'] == 'C001':
        return ep.EvidenceExtraction(
            facts=[ep.ExtractedFact(
                event='星河项目', period='2024', claim='担任总负责人', answer='yes',
                sources=[ep.FactSource(chunk_id='ca1', quote='担任星河项目总负责人')],
            )],
            fully_supported=True, missing_information=[],
        )
    if requirement['requirement_id'] == 'S1':
        return ep.EvidenceExtraction(
            facts=[ep.ExtractedFact(
                event='星河项目', period='2024', claim='参与项目', answer='yes',
                sources=[ep.FactSource(chunk_id='cb1', quote='参与星河项目')],
            )],
            fully_supported=True, missing_information=[],
        )
    # S2：有命中分块但无相关证据
    return ep.EvidenceExtraction(facts=[], fully_supported=False,
                                 missing_information=['没有项目经理信息'])


def test_build_evidence_packs_grouping_dedup_and_statuses():
    ep = module()
    packs = ep.build_evidence_packs(
        candidate_ids=['C001', 'C002', 'C001'],   # 重复工号应被去重
        requirements=[
            {'requirement_id': 'S1', 'query': '负责过星河项目'},
            {'requirement_id': 'S2', 'query': '担任过项目经理'},
        ],
        sources=_pack_rows(),
        extract=_pack_extract,
    )

    # 1) C001 重复工号只生成一个包
    assert [p['candidate_id'] for p in packs] == ['C001', 'C002']
    assert all(p['schema_version'] == '2.0' for p in packs)

    # 2) C001-S1 充分
    c001, c002 = packs
    assert c001['requirements'][0]['status'] == 'sufficient'
    assert c001['requirements'][0]['reason'] == 'evidence_review'
    assert c001['requirements'][0]['extraction_status'] == 'succeeded'
    assert len(c001['requirements'][0]['facts']) == 1
    assert c001['requirements'][0]['conflicts'] == []
    # 来源校验后带起止位置
    ref = c001['requirements'][0]['facts'][0]['sources'][0]
    assert ref['chunk_id'] == 'ca1'
    # quote='担任星河项目总负责人' 在 content='张伟担任星河项目总负责人。' 中从下标 2 开始
    assert ref['quote_start'] == 2
    # citations 保留命中分块（ca1、ca2、ca3 均命中 S1）
    assert len(c001['requirements'][0]['citations']) == 3

    # 3) C001-S2 有命中但无相关证据 -> missing / no_relevant_evidence
    assert c001['requirements'][1]['status'] == 'missing'
    assert c001['requirements'][1]['reason'] == 'no_relevant_evidence'

    # 4) C002-S1 充分（其分块被正确路由到 C002）
    assert c002['requirements'][0]['status'] == 'sufficient'
    assert c002['requirements'][0]['facts'][0]['sources'][0]['chunk_id'] == 'cb1'

    # 5) C002-S2 无命中分块 -> missing / no_accessible_hits / not_run
    s2 = c002['requirements'][1]
    assert s2['status'] == 'missing'
    assert s2['reason'] == 'no_accessible_hits'
    assert s2['extraction_status'] == 'not_run'
    assert s2['citations'] == []