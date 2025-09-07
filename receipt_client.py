# langchain_mcp_receipt_client.py
import asyncio
import os
import json
from dotenv import load_dotenv

from mcp import ClientSession
from mcp.client.sse import sse_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.memory import ConversationSummaryBufferMemory
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

load_dotenv()

# ---------------------------
# JSON encoder
# ---------------------------
class CustomEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, "content"):
            return {"type": o.__class__.__name__, "content": o.content}
        return super().default(o)

# ---------------------------
# Gemini LLM
# ---------------------------
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0,
    max_retries=2,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
)

memory = ConversationSummaryBufferMemory(
    llm=llm, return_messages=True, max_token_limit=1000
)

async def run_agent():
    async with sse_client("http://127.0.0.1:8000/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            agent = create_react_agent(llm, tools)

            print("MCP Receipt Client Started! Type 'quit' to exit.")
            memory.chat_memory.add_message(SystemMessage(
                content="You are a receipt assistant connected to MCP OCR tools."
            ))

            while True:
                query = input("\nQuery: ").strip()
                if query.lower() == "quit" or query.lower() == "exit":
                    break

                memory.chat_memory.add_message(HumanMessage(content=query))
                past_messages = memory.load_memory_variables({})["history"]

                response = await agent.ainvoke(
                    {"messages": past_messages + [HumanMessage(content=query)]}
                )

                memory.chat_memory.add_message(AIMessage(content=str(response)))
                try:
                    formatted = json.dumps(response, indent=2, cls=CustomEncoder)
                except Exception:
                    formatted = str(response)

                print("\nResponse:")
                print(formatted)

if __name__ == "__main__":
    asyncio.run(run_agent())
