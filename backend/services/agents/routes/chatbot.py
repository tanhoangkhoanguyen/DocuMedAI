from fastapi import APIRouter, status, Body, HTTPException


from backend.main import run_graph, emotionGraph


router = APIRouter()


@router.post(
    "/chatbot",
    response_model = str,
    status_code = status.HTTP_200_OK
)
async def chatbot_test(request: str = Body(...)) -> str:
    """
        lawAdvisory chatbot that provide user with info from the American laws
    """
    try:
        bot_reply = run_graph(request)
        return bot_reply
        # {
        #     "bot_reply": bot_reply
        # }
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"{e}")