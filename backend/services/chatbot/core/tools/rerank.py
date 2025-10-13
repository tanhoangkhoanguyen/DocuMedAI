import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


class ReRanker:
    def __init__(self, rerank_model:str="BAAI/bge-reranker-v2-m3"):
        try:
            self.__tokenizer = AutoTokenizer.from_pretrained(rerank_model)
            self.__device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.__model = AutoModelForSequenceClassification.from_pretrained(rerank_model).eval().to(self.__device)
        except Exception as e:
            print(f"[ERROR] From ReRanker initializaiton: {str(e)}")

    def remove_similar_documents(self, documents):
        unique_docs = list(set(documents))
        result = []
        for doc in unique_docs:
            is_substring = False
            for other in unique_docs:
                if doc != other and doc in other:
                    is_substring = True
                    break
            if not is_substring:
                result.append(doc)
        return result
    
    def rerank(self, user_message, passages, top_k:int=5, threshold:int=-5):
        pairs = [[user_message, passage] for passage in passages]
        inputs = self.__tokenizer(pairs, padding = True, truncation = True, return_tensors = "pt").to(self.__device)
        inputs = inputs.to(self.__device)
        with torch.no_grad():
            scores = self.__model(**inputs).logits.squeeze(-1)
        sorted_indices = torch.argsort(scores, descending = True)
        reliable_docs = []
        unreliable_docs = []
        bound = min(top_k, len(sorted_indices))
        for i in sorted_indices[:bound]:
            if scores[i].item() >= threshold:
                reliable_docs.append(passages[i])
            else:
                unreliable_docs.append(passages[i])
        return reliable_docs, unreliable_docs