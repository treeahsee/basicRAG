import os
import json
from pydantic import BaseModel
from langchain.chat_models import init_chat_model
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict
from langchain_core.prompts import ChatPromptTemplate
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone


def handler(event, context):
    ai_key = os.getenv("OPENAI_API_KEY")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large", api_key=ai_key)

    pc_api_key = os.getenv("PINECONE_API_KEY")
    pc = Pinecone(api_key = pc_api_key)
    index_name = "marketo"

    index = pc.Index(index_name)

    vector_store = PineconeVectorStore(index=index, embedding=embeddings)

    llm = init_chat_model("gpt-4o-mini", model_provider="openai")

    prompt = ChatPromptTemplate.from_template("""Answer the question based only on 
                                                the following context:
                                                {context}

                                                Question: {question}
                                                """)
    
    class State(TypedDict):
        question: str
        context: List[Document]
        answer: str


    def retrieve(state: State):
        retrieved_docs = vector_store.similarity_search(event['body'],k=1)
        return {"context": retrieved_docs}


    def generate(state: State):
        docs_content = "\n\n".join(doc.page_content for doc in state["context"])
        messages = prompt.invoke({"question": state["question"], "context": docs_content})
        response = llm.invoke(messages)
        return {"answer": response.content}
    
    graph_builder = StateGraph(State).add_sequence([retrieve, generate])
    graph_builder.add_edge(START, "retrieve")
    graph = graph_builder.compile()

    # Define the request body model
    class QueryRequest(BaseModel):
        question: str

    # Define the response format
    class QueryResponse(BaseModel):
        # context: List[str]
        answer: str
    
    result = graph.invoke({"question": event['body']})
    answer = result['answer']
    ctx_doc = set([result['context'][i].metadata['source'] for i in range(len(result['context']))])

    return {
        "statusCode" : 200,
        "body" : json.dumps({"answer": answer, "document": list(ctx_doc)}),
    }