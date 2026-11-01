import logging

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_openai import ChatOpenAI

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_chat_model(model_name: str | None = None, temperature: float = 0.2, settings: Settings | None = None) -> ChatOpenAI | None:
    """获取一个基于 DashScope 兼容接口的对话模型（ChatOpenAI 包装）。

    使用 OpenAI 兼容模式把 DashScope 的对话模型包装成 LangChain Chat Model，
    便于后续用于结构化输出等能力。未配置 API Key 时返回 None。

    Args:
        model_name: 可选，覆盖默认模型名。
        temperature: 生成随机度（kimi 系列模型不支持该参数，会传 None）。
        settings: 可选，配置对象；缺省使用全局缓存的配置。

    Returns:
        配置好的 ChatOpenAI 实例；未配置 Key 时返回 None。
    """
    value = settings or get_settings()
    # 未配置 API Key（或仍为占位符）时无法调用模型，返回 None
    if not value.dashscope_api_key or value.dashscope_api_key == "请用户自行填写":
        return None
    model = model_name or value.chat_model
    return ChatOpenAI(
        model=model,
        api_key=value.dashscope_api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # DashScope 的 OpenAI 兼容端点
        temperature=None if "kimi" in model else temperature,  # kimi 模型不支持此参数
    )


def get_judge_model(
    model_name: str | None = None,
    temperature: float = 0.2,
    settings: Settings | None = None,
) -> ChatOpenAI | None:
    """获取评测用的 Judge 模型（可与对话模型分开配置）。

    Judge 走独立的模型名 / API Key / 端点，便于把"被测模型"和"评分模型"
    分离开，避免自评。三项配置任一为空时返回 None。

    Args:
        model_name: 可选，覆盖配置中的 judge_model。
        temperature: 生成随机度。
        settings: 可选，配置对象；缺省使用全局缓存的配置。

    Returns:
        配置好的 ChatOpenAI 实例；未配置完整时返回 None。
    """
    value = settings or get_settings()
    model = model_name or value.judge_model
    if not value.judge_api_key or not value.judge_base_url or not model:
        logger.info("judge_model_not_configured")
        return None
    return ChatOpenAI(
        model=model,
        api_key=value.judge_api_key,
        base_url=value.judge_base_url,
        temperature=temperature,
    )


def get_embedding_model(settings: Settings | None = None) -> DashScopeEmbeddings | None:
    """获取向量化（Embedding）模型，未配置 API Key 时返回 None。"""
    value = settings or get_settings()
    if not value.dashscope_api_key or value.dashscope_api_key == "请用户自行填写":
        return None
    return DashScopeEmbeddings(model=value.embedding_model, dashscope_api_key=value.dashscope_api_key)

if __name__ == "__main__":
    # 以下为本地调试入口（非正式逻辑），用于验证模型可调用
    model = get_chat_model()
    # res = model.invoke(
    #         [("user", "你是谁？")]
    #     )
    from app.talent.talent_evaluation_dispatch import EvaluationDimensionPlan, DIMENSION_GENERATOR_PROMPT

    # 用结构化输出定义一个评估维度计划对象并调用模型
    res = model.with_structured_output(EvaluationDimensionPlan).invoke(
                [
                    ("system", DIMENSION_GENERATOR_PROMPT),
                    ("user", "你好"),
                ]
            )
    print(res)