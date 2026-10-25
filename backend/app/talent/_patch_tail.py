# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"E:\PythonProjects\talent-eval-agents\talent-eval-agents\backend\app\talent\talent_evaluation_dispatch.py")
text = p.read_text(encoding="utf-8")

pairs = [
    (
        'def _receive_input(\n    state: TalentEvaluationDispatchState,\n    runtime: Runtime[DecisionContext],\n) -> dict[str, Any]:\n    logger.info(',
        'def _receive_input(\n    state: TalentEvaluationDispatchState,\n    runtime: Runtime[DecisionContext],\n) -> dict[str, Any]:\n    """接收并校验评估输入：校验上下文、人才请求与查询计划。"""\n    logger.info(',
    ),
    (
        'def _generate_dimensions(dimension_generator: DimensionGenerator):\n    def generate_dimensions(',
        'def _generate_dimensions(dimension_generator: DimensionGenerator):\n    """构造“生成维度”节点：调用维度生成器生成评估维度计划。"""\n    def generate_dimensions(',
    ),
    (
        'def _validate_dimensions(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    plan = EvaluationDimensionPlan(',
        'def _validate_dimensions(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    """校验维度计划（权重合计是否合法）。"""\n    plan = EvaluationDimensionPlan(',
    ),
    (
        'def _route_dimension_validation(\n    state: TalentEvaluationDispatchState,\n) -> Literal["retrieve_candidates", "prepare_work_items", "dimension_invalid"]:\n    if state.get("validation_issues"):',
        'def _route_dimension_validation(\n    state: TalentEvaluationDispatchState,\n) -> Literal["retrieve_candidates", "prepare_work_items", "dimension_invalid"]:\n    """维度校验后的路由：有问题→维度非法；已预选→进入工作项；否则检索候选人。"""\n    if state.get("validation_issues"):',
    ),
    (
        'def _retrieve_candidates(candidate_provider: CandidateProvider):\n    def retrieve_candidates(',
        'def _retrieve_candidates(candidate_provider: CandidateProvider):\n    """构造“检索候选人”节点：调用候选人提供者筛选候选人。"""\n    def retrieve_candidates(',
    ),
    (
        'def _prepare_work_items(max_work_items: int):\n    def prepare_work_items(',
        'def _prepare_work_items(max_work_items: int):\n    """构造“准备工作项”节点：生成候选人×维度的取证工作项并做容量检查。"""\n    def prepare_work_items(',
    ),
    (
        'def build_work_item_sends(\n    work_items: list[dict[str, Any]],\n) -> list[Send]:\n    logger.info(',
        'def build_work_item_sends(\n    work_items: list[dict[str, Any]],\n) -> list[Send]:\n    """将工作项转换为 Send 指令，以并行分派给取证分支节点。"""\n    logger.info(',
    ),
    (
        'def _dispatch_work_items(state: TalentEvaluationDispatchState):\n    if state.get("status") == "capacity_exceeded":',
        'def _dispatch_work_items(state: TalentEvaluationDispatchState):\n    """工作项分派路由：超限→结束；无工作项→无候选人；否则并行 Send。"""\n    if state.get("status") == "capacity_exceeded":',
    ),
    (
        'def _run_assessment_branch(branch_worker: BranchWorker):\n    def run_assessment_branch(',
        'def _run_assessment_branch(branch_worker: BranchWorker):\n    """构造“取证分支”节点：为单个工作项执行证据检索，并以失败兜底。"""\n    def run_assessment_branch(',
    ),
    (
        'def _finalize(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    has_failures = any(',
        'def _finalize(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    """汇总分支结果：依据是否存在失败分支设定最终状态。"""\n    has_failures = any(',
    ),
    (
        'def _dimension_invalid(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    return {',
        'def _dimension_invalid(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    """维度非法：记录校验问题的错误信息并结束。"""\n    return {',
    ),
    (
        'def _no_candidates(_: TalentEvaluationDispatchState) -> dict[str, Any]:\n    return {',
        'def _no_candidates(_: TalentEvaluationDispatchState) -> dict[str, Any]:\n    """无候选人：清空工作项与分支结果。"""\n    return {',
    ),
    (
        'def _capacity_exceeded(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    return {',
        'def _capacity_exceeded(state: TalentEvaluationDispatchState) -> dict[str, Any]:\n    """容量超限：返回错误信息。"""\n    return {',
    ),
    (
        'def build_talent_evaluation_dispatch_graph(\n    *,\n    dimension_generator: DimensionGenerator,\n    candidate_provider: CandidateProvider,\n    branch_worker: BranchWorker,\n    max_work_items: int = 24,\n):\n    if max_work_items < 1:',
        'def build_talent_evaluation_dispatch_graph(\n    *,\n    dimension_generator: DimensionGenerator,\n    candidate_provider: CandidateProvider,\n    branch_worker: BranchWorker,\n    max_work_items: int = 24,\n):\n    """组装并编译“评估分派执行”子图。\n\n    流程：接收输入 → 生成/校验维度 → （检索候选人）→ 准备工作项 →\n    并行分派取证分支 → 汇总（finalize）。\n    """\n    if max_work_items < 1:',
    ),
]

for old, new in pairs:
    cnt = text.count(old)
    assert cnt == 1, f"anchor count {cnt} for: {old[:60]!r}"
    text = text.replace(old, new)

p.write_text(text, encoding="utf-8")
print("patched OK; anchors replaced:", len(pairs))