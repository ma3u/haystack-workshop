"""
Exercise 4: Haystack Agent with Tools (NEW in 2.x)
Goal: Use Agent abstraction for tool-calling LLMs.

Tasks:
1. Define Tool with function + schema
2. Create Agent with chat_generator + tools
3. Run multi-step reasoning
"""
import os
from dotenv import load_dotenv
from haystack.components.agents import Agent
from haystack.dataclasses import ChatMessage
from haystack.tools import Tool

from llm import chat_generator

load_dotenv()


def get_weather(city: str) -> str:
    """Fake weather lookup."""
    data = {"Berlin": "12C rainy", "Lisbon": "22C sunny", "Oslo": "4C snow"}
    return data.get(city, "unknown")


def calculate(expression: str) -> float:
    """Evaluate math expression safely."""
    return eval(expression, {"__builtins__": {}}, {})


weather_tool = Tool(
    name="get_weather",
    description="Get current weather for a city",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    function=get_weather,
)

calc_tool = Tool(
    name="calculate",
    description="Evaluate arithmetic expression",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
    function=calculate,
)

agent = Agent(
    chat_generator=chat_generator(),
    tools=[weather_tool, calc_tool],
    system_prompt="You use tools to answer questions. Be concise.",
    max_agent_steps=5,
)

if __name__ == "__main__":
    agent.warm_up()
    result = agent.run(messages=[
        ChatMessage.from_user("What's the weather in Berlin? Also what's 23 * 47?")
    ])
    print(result["messages"][-1].text)
