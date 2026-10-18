"""
Exercise 6: Streaming Chat Pipeline
Goal: Stream LLM tokens via callback.

Tasks:
1. Define streaming_callback
2. Use OpenAIChatGenerator with streaming
3. Build multi-turn chat loop
"""
import os
from dotenv import load_dotenv
from haystack.dataclasses import ChatMessage, StreamingChunk

from llm import chat_generator

load_dotenv()


def print_streaming(chunk: StreamingChunk):
    print(chunk.content, end="", flush=True)


chat = chat_generator(streaming_callback=print_streaming)

if __name__ == "__main__":
    history = [
        ChatMessage.from_system("You are a terse technical mentor."),
    ]
    while True:
        q = input("\n> ")
        if q.lower() in ("exit", "quit"):
            break
        history.append(ChatMessage.from_user(q))
        result = chat.run(messages=history)
        reply = result["replies"][0]
        history.append(reply)
