import os
import json
import hashlib
import argparse
import datetime
from pydantic import BaseModel
from langchain.chat_models import init_chat_model
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import WebBaseLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict
from langchain_core.prompts import ChatPromptTemplate
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")


embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

pc = Pinecone(api_key = PINECONE_API_KEY)
index_name = "marketo" # cli arg

if not pc.has_index(index_name):
    print("Creating new index")
    pc.create_index(
        name=index_name,
        # vector_type="dense",
        dimension=3072, 
        metric="cosine", 
        spec=ServerlessSpec(
            cloud="aws",
            region="us-east-1"
        ) 
    )
else:
    print(f"Index: '{index_name}' Already Exists")

index = pc.Index(index_name)
vector_store = PineconeVectorStore(index=index, embedding=embeddings)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,  # chunk size (characters)
    chunk_overlap=200,  # chunk overlap (characters)
    add_start_index=True,  # track index in original document
)

# Define file and URL locations
PDF_DIR = "./pdfs"
URLS_FILE = "urls.txt"

def get_file_list():
    """Retrieve a list of local PDF files."""
    return [os.path.join(PDF_DIR, f) for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]


def get_urls():
    """Retrieve a list of URLs from a text file."""
    if not os.path.exists(URLS_FILE):
        return []
    with open(URLS_FILE, "r") as f:
       return [line.strip().strip('"').strip("'") for line in f if line.strip()]

    
def get_file_hash(filepath):
    """Generate a hash of the file contents for change detection."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()

def already_indexed(source_list, source_type = "file"):
    """Check which sources are already in the index and return new/updated ones."""
    to_add = []
    for source in source_list:
        results = index.query(vector=[0]*3072, top_k=10, filter={"source": {"$eq": source}}, include_metadata=True)
        
        if len(results['matches']) == 0:
            print(f"New source detected: {source}")
            to_add.append(source)
        else:
            existing_metadata = results["matches"][0].get("metadata", {})
            if source_type == "file":
                file_hash = get_file_hash(source)
                if existing_metadata.get("hash") != file_hash:
                    print(f"Updated source detected: {source}")
                    to_add.append(source)
                else:
                    print(f"{source} file hasnt changed")
            else: 
                print(f"{source} exists in index already")

    return to_add

def process_and_store_documents(source_list, source_type="file"):
    """Load, split, and store documents in the vector store."""
    for source in source_list:
        loader = PyPDFLoader(source) if source_type == "file" else WebBaseLoader(source)
        documents = loader.load()
        docs = text_splitter.split_documents(documents)

        metadata = {"source": source, "timestamp": datetime.datetime.now(datetime.UTC).isoformat()}
        if source_type == "file":
            metadata["hash"] = get_file_hash(source)

        for doc in docs:
            doc.metadata.update(metadata)

        print(f"Adding {source} to vector store")
        vector_store.add_documents(docs)

def delete_outdated_entries():
    """Remove outdated sources from Pinecone if they no longer exist in the local repo."""
    existing_files = get_file_list()
    existing_urls = get_urls()

    results = index.query(vector=[0]*3072, top_k=10000, include_metadata=True)

    sources = set([match["metadata"].get("source") for match in results["matches"] if match["metadata"].get("source") not in existing_files and source not in existing_urls])
    for source in sources:
        delete_from_index(source)

def delete_from_index(delete_source):
    results = index.query(vector=[0]*3072, top_k=10000, filter={"source": {"$eq": delete_source}}, include_metadata=True)
    ids_to_delete = [match["id"] for match in results["matches"]]
    if ids_to_delete:
        index.delete(ids=ids_to_delete)
        print(f"Deleted {len(ids_to_delete)} vectors from source: {delete_source}")
    else:
        print("No vectors found for the specified source.") 

def main(sync=False, cleanup=False, delete_source=None):
    """Main function to update the vector store."""
    if delete_source:
        delete_from_index(delete_source)
        return

    if cleanup:
        delete_outdated_entries()

    if sync:
        files_to_add = already_indexed(get_file_list(), "file")
        urls_to_add = already_indexed(get_urls(), "web")

        if files_to_add:
            process_and_store_documents(files_to_add, "file")
        if urls_to_add:
            process_and_store_documents(urls_to_add, "web")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update Pinecone vector store")
    parser.add_argument("--sync", action="store_true", help="Sync new and updated documents")
    parser.add_argument("--cleanup", action="store_true", help="Remove outdated documents from index")
    parser.add_argument("--delete", type=str, help="Delete a specific PDF file or URL from the index")
    args = parser.parse_args()

    main(sync=args.sync, cleanup=args.cleanup, delete_source=args.delete)
