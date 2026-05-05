from fastapi import FastAPI
from pydantic import BaseModel
from text_to_sql import ask
from agent import run as agent_run, _vector_search

app = FastAPI(title="usaspending-ai")


class Q(BaseModel):
    q: str


class SearchQ(BaseModel):
    q: str
    k: int = 10
    table_name: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/ask")
def ask_endpoint(body: Q) -> dict:
    return ask(body.q)


@app.post("/search")
def search_endpoint(body: SearchQ) -> dict:
    return {"results": _vector_search(body.q, body.k, body.table_name)}


@app.post("/agent")
def agent_endpoint(body: Q) -> dict:
    return agent_run(body.q)
