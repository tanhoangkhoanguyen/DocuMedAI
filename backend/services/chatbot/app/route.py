from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage

from services.chatbot.app.request_schemas import ChatRequest, ChatResponse

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    try:
        human_msg = HumanMessage(content=req.message)
        init_state = {"chat_history": [human_msg]}

        try:
            result = router.graph.invoke(
                input=init_state,
                config={"configurable": {"thread_id": req.session_id}}
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.services.chatbot.app.route] :
                \t{str(e)}
            """)

        ai_msg = result["chat_history"][-1]
        if isinstance(ai_msg, AIMessage):
            reply_text = ai_msg.content
        else:
            reply_text = "Sorry, I didn't understand that."

        return ChatResponse(
            session_id=req.session_id,
            topic_id=result.get("topic_id", -1),
            response=reply_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))