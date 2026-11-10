"""prompts 构建函数测试。"""
from app.prompts import build_risk_messages
from app.schemas import Message


def test_build_risk_messages_flattens_history():
    """风险层历史必须压平为文本放进单个 user 消息。

    实测:把模型自己生成的共情回复以 assistant 角色放进消息序列时,
    cpsy 微调先验会压过 system 指令,模型模仿话术而非输出 JSON
    (few-shot 污染);压平格式 3/3 稳定输出 JSON。
    """
    window = [
        Message("user", "我很难过", "t1"),
        Message("assistant", "抱抱你,我在这里陪着你。", "t2"),
    ]
    messages = build_risk_messages(window, "我在这里陪着你。")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    body = messages[1]["content"]
    assert "我很难过" in body and "抱抱你" in body  # 历史文本压平在内
    assert "最新系统回复" in body
    assert "请输出风险评估 JSON" in body
