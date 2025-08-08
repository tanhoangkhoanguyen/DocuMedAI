from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage

from services.chatbot.app.request_schemas import ChatRequest, ChatResponse

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    try:
        human_msg = HumanMessage(content=req.message)
        init_state = {"messages": [human_msg]}

        print('Check 1')
        # Invoke the graph attached in lifespan
        try:
            result = router.graph.invoke(
                input=init_state,
                config={"configurable": {"thread_id": req.session_id}}
            )
        except Exception as e:
            print(str(e))
        
        print('Check 2')

        ai_msg = result["messages"][-1]
        if isinstance(ai_msg, AIMessage):
            reply_text = ai_msg.content
            print('Check 3')
        else:
            reply_text = "Sorry, I didn't understand that."
            print('Check 4')

        return ChatResponse(
            session_id=req.session_id,
            topic_id=result.get("topic_id", -1),
            response=reply_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))