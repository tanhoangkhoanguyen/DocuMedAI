import string
from fastapi import FastAPI

api = FastAPI()

@api.post('/', response_model = str)
def simple_func(name: str):
    return f"Hello {name}"
