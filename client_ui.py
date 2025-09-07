import streamlit as st
import os
import asyncio
import json
import tempfile

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.sse import sse_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.memory import ConversationSummaryBufferMemory
from langchain_core.messages import HumanMessage, SystemMessage

# =========================================================
# SETUP
# =========================================================
load_dotenv()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0,
    max_retries=2,
    google_api_key=os.getenv("GOOGLE_API_KEY"),
)

memory = ConversationSummaryBufferMemory(
    llm=llm, return_messages=True, max_token_limit=1000
)

# =========================================================
# HELPER: Run agent with query
# =========================================================
async def run_agent_with_query(query: str):
    async with sse_client("http://127.0.0.1:8000/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            agent = create_react_agent(llm, tools)

            memory.chat_memory.add_message(SystemMessage(
                content=(
                    "You are an Expense Tracking Assistant. "
                    "Your job is to process receipt images, extract their text using the OCR tool, "
                    "convert the text into structured JSON with fields: vendor, date, total_amount, and line_items, "
                    "categorize each item using the available categories, and append the structured result into Google Sheets. "
                    "\n\n"
                    "Always use the provided MCP tools for: \n"
                    "- `extract_receipt_text` → to OCR the receipt image\n"
                    "- `structure_receipt_text` → to turn raw text into structured JSON\n"
                    "- `append_to_sheet` → to save structured data into Google Sheets\n"
                    "- `add_category` / `remove_category` → to manage spending categories\n\n"
                    
                    "Dates should be in DD/MM/YYYY format when possible. "
                    "Keep vendor names short and consistent. "
                    "Be robust to noisy OCR text. "
                    "If categories do not match exactly, choose the closest one or ask the user to add a new one."
                )
                ))
            past_messages = memory.load_memory_variables({})["history"]
            response = await agent.ainvoke(
                {"messages": past_messages + [HumanMessage(content=query)]}
            )
            print(str(response["messages"][2].content))
            return str(response["messages"][2].content)
            # ✅ Extract last AIMessage only
            # if isinstance(response, list):
            #     ai_messages = [m for m in response if m.__class__.__name__ == "AIMessage"]
            #     if ai_messages:
            #         print("I am here")
            #         return ai_messages[-1].content
                
            # if hasattr(response, "content"):
            #     return response.content

            # return str(response)  # fallback


# =========================================================
# STREAMLIT UI
# =========================================================
st.set_page_config(page_title="Receipt Assistant", layout="wide")
st.title("🧾 Receipt Chatbot")

# Chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Display chat history
for msg in st.session_state["messages"]:
    role, content = msg
    with st.chat_message(role):
        st.markdown(content)

# Upload image
uploaded_file = st.file_uploader("Upload a receipt image", type=["jpg", "jpeg", "png"])

# Chat input
if prompt := st.chat_input("Type your query here..."):
    # Save query to history
    st.session_state["messages"].append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    # If image uploaded, save temporarily and include its path in the query
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(uploaded_file.read())
            image_path = tmp.name
        query = f"{prompt}\nReceipt path: {image_path}"
    else:
        query = prompt

    # Run agent
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            final_output = asyncio.run(run_agent_with_query(query))
            st.markdown(final_output)

    st.session_state["messages"].append(("assistant", final_output))


