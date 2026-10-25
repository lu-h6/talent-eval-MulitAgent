from langchain_community.embeddings import DashScopeEmbeddings
from langchain_openai import ChatOpenAI

from app.core.config import Settings, get_settings


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