from playground.khoanth.chatbot.core.constants.schemas import GraphState, MessageAnalysisState, NodeControllerState
from playground.khoanth.chatbot.core.constants.prompts import MESSAGE_ANALYSIS_PROMPT_1, MESSAGE_ANALYSIS_PROMPT_2, MEMORY_CONTROLLER_PROMPT_1, MEMORY_CONTROLLER_PROMPT_2, INTENT_ANALYSIS_PROMPT_1, INTENT_ANALYSIS_PROMPT_2, SYNTHESIS_PROMPT
from playground.khoanth.chatbot.core.agents.law_support import lawSupporter
from playground.khoanth.chatbot.core.agents.chit_chat import ChitChater
from playground.khoanth.chatbot.core.agents.instruction_support import InstructionSupporter
from services.chatbot.core.tools.rerank import ReRanker

from dotenv import load_dotenv
load_dotenv()

import os, uuid, pytz, queue
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from cryptography.fernet import Fernet
from datetime import datetime

class MessageAnalysis(Runnable):
    def __init__(
            self, 
            chat_model:str,
            temperature:int = 0
        ):
        self.__llm = ChatOpenAI(model_name = chat_model, temperature = temperature)

    def invoke(self, state:GraphState, config = None):
        user_message = state.chat_history[-1].content
        prompt = [
            SystemMessage(content = MESSAGE_ANALYSIS_PROMPT_1),
            SystemMessage(content = MESSAGE_ANALYSIS_PROMPT_2),
            HumanMessage(content = f"USER MESSAGE: {user_message}")
        ]
        response = self.__llm.with_structured_output(MessageAnalysisState).invoke(prompt)
        state.user_inputs = response.user_inputs
        return state

class NodeController(Runnable):
    def __init__(
            self, 
            chat_model:str, 
            embedding_model: str,
            max_workers: int,
            temperature:int = 0,
            timeout = None
        ):
        self.__llm = ChatOpenAI(model_name = chat_model, temperature = temperature)
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__max_workers = max_workers
        self.__qdrant_client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY"),
            timeout = timeout
        )
        self.__mongodb_client = MongoClient(
            os.getenv("MONGODB_URI"), 
            server_api = ServerApi('1')
        )["lawAdvisory"]["chat_pool"]
        self.__cryptography_f = Fernet(os.getenv("CRYPTOGRAPHY_KEY"))
        self.__reranker = ReRanker
        self.__chit_chat = ChitChater(chat_model = chat_model)
        self.__law_support = lawSupporter(chat_model = chat_model)
        self.__instruction_support = InstructionSupporter()

    def __chat_pool_retrieval(self, query, collection_name = "chat_pool", top_k:int = 1):
        embedded_query = self.__embedding_model.embed_query(query)
        results = self.__qdrant_client.search(
            collection_name = collection_name,
            query_vector = embedded_query,
            limit = top_k,
            with_payload = True,
            with_vectors = False
        )
        return results

    def __hierarchy_retrieval(self, query, conversation_id, top_k:int = 24):
            q = queue.Queue()
            visited = set()
            cnt = 0
            results = []

            q.put(conversation_id)
            visited.add(conversation_id)
            while q.empty() == False and cnt < top_k:
                child = q.get()
                cnt += 1
                temp_dict = self.__mongodb_client.find_one({"conversation_id": child})
                temp_str = self.__cryptography_f.decrypt(temp_dict["message"].encode()).decode()
                results.append(temp_str)

                for ancestor in temp_dict["ancestors"]:
                    if ancestor not in visited and ancestor != "0":
                        q.put(ancestor)
                        visited.add(ancestor)
            bound = max(5, len(results))
            temp_a, temp_b = self.__reranker.rerank(query, results)
            return ' '.join(temp_a) + ' ' + ' '.join(temp_b)

    def invoke(self, state:GraphState, max_workers:int = 8, threshold:float = 0.25, config = None):
        def normalize_length(array, desired_length):
            if len(array) > desired_length:
                return array[:desired_length]
            if len(array) < desired_length:
                while len(array) < desired_length:
                    array.append(0)
            return array
        
        def preprocess(user_input):
            nodes = []
            prompt = [
                SystemMessage(content = MEMORY_CONTROLLER_PROMPT_1.format(number = len(user_input.messages))),
                SystemMessage(content = MEMORY_CONTROLLER_PROMPT_2),
                HumanMessage(content = f"Global Context: {global_context}"),
                HumanMessage(content = f"""
                    Local Context: {user_input.context}
                    User Messages: {user_input.messages}
                """)]
            response = self.__llm.with_structured_output(NodeControllerState).invoke(prompt)
            memory_list = normalize_length(response.unit, len(user_input.messages))
            for idx in memory_list:
                if memory_list[idx] != 1:
                    state.ancestors.add(state.current_node)
            
                user_query = user_input.context + '\n' + user_input.messages[idx]
                prev_conversation = global_context + '\n' + user_query
                if memory_list[idx] == 0:
                    nodes.append(prev_conversation)
                else:
                    result = self.__chat_pool_retrieval(user_query)
                    try:
                        if result[0].score < threshold:
                            raise Exception("[ERROR] From NodeController.invoke: This is a custom error.")

                        conversation_id = result[0].payload["conversation_id"]
                        state.ancestors.add(conversation_id)
                        tree_context = self.__hierarchy_retrieval(user_query, conversation_id)
                        
                        if memory_list[idx] == 1:
                            nodes.append(tree_context + '\n' + user_query)
                        if memory_list[idx] == 2:
                            nodes.append(tree_context + '\n' + prev_conversation)
                    except:
                        if memory_list[idx] == 1:
                            nodes.append(user_query)
                        if memory_list[idx] == 2:
                            nodes.append(prev_conversation)
            return nodes

        def agents_call():
            if node[1] == 0:
                return self.__chit_chat.executor(node[0])
            if node[1] == 1:
                return self.__law_support.executor(node[0])
            return self.__instruction_support.executor()

        user_message = state.chat_history[-1].content
        nodes = []
        global_context = "\n".join(state.global_context)
        with ThreadPoolExecutor(max_workers = max_workers) as pool:
            nodes = list(pool.map(preprocess, state.user_inputs))
        prompt = [
            SystemMessage(content = INTENT_ANALYSIS_PROMPT_1.format(number = len(nodes))),
            SystemMessage(content = INTENT_ANALYSIS_PROMPT_2),
            HumanMessage(content = nodes)
        ]
        response = self.__llm.with_structured_output(NodeControllerState).invoke(prompt)
        intent_list = normalize_length(response.unit, len(nodes))
        agents_list = list(zip(nodes, intent_list))
        with ThreadPoolExecutor(max_workers = max_workers) as pool:
            results = list(pool.map(agents_call, agents_list))
        prompt = [
            SystemMessage(content = SYNTHESIS_PROMPT),
            HumanMessage(content = f"CONTEXT LIST:\n{results}"),
            HumanMessage(content = user_message)
        ]
        response = self.__llm.invoke(prompt)
        state.chat_history.append(response)
        return state

class SchemaResetNode(Runnable):
    def __init__(
            self, 
            chat_model:str,
            embedding_model:str,
            temperature:int = 0,
            timeout = None
        ):
        self.__llm = ChatOpenAI(model_name = chat_model, temperature = temperature)
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))
        self.__cryptography_f = Fernet(os.getenv("CRYPTOGRAPHY_KEY"))
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__qdrant_client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY"),
            timeout = timeout
        )
        self.__mongodb_client = MongoClient(
            os.getenv("MONGODB_URI"), 
            server_api = ServerApi('1')
        )["lawAdvisory"]["chat_pool"]

    def __upload_to_qdrant(self, conversation_id:str, query:str, collection_name:str = "chat_pool"):
        vector = self.__embedding_model.embed_query(query)
        point = PointStruct(
            id = conversation_id,
            vector = vector,
            payload = {
                "conversation_id": conversation_id
            }
        )
        self.__qdrant_client.upsert(
            collection_name = collection_name,
            points = [point]
        )
    
    def __upload_to_mongodb(self, conversation_id:str, query:str, ancestors):
        mongodb_message = self.__cryptography_f.encrypt(query.encode()).decode()
        if len(ancestors) == 0:
            ancestors = ['0']
        self.__mongodb_client.insert_one({
            "conversation_id": conversation_id,
            "message": mongodb_message,
            "ancestors": ancestors
        })

    def invoke(self, state:GraphState, top_k:int = 1, config=None):
        conversation_id = str(uuid.uuid5(self.__namespace, str(datetime.now(pytz.utc))))
        original_message = state.chat_history[-2].content + '\n' + state.chat_history[-1].content
        self.__upload_to_qdrant(conversation_id, original_message)
        self.__upload_to_mongodb(conversation_id, original_message, list(state.ancestors))        
        
        if len(state.global_context) == top_k:
            state.global_context.pop(0)
        state.global_context.append(original_message)
        
        state.global_instruction = ""
        state.user_inputs = []
        state.current_node = conversation_id
        state.ancestors = set()
        return state



