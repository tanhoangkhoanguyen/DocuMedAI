# from transformers import AutoTokenizer, AutoModelForSequenceClassification
# import torch

# # Load tokenizer and model
# model_name = "BAAI/bge-reranker-v2-m3"
# tokenizer = AutoTokenizer.from_pretrained(model_name)
# model = AutoModelForSequenceClassification.from_pretrained(model_name).eval()  # evaluation mode

# # Move model to GPU if available
# device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# model = model.to(device)

# def rerank(query, passages, top_k=5):
#     """
#     Rerank a list of passages given a query using BGE reranker.

#     Args:
#         query (str): The user query.
#         passages (List[str]): List of retrieved passages.
#         top_k (int): Number of top passages to return after reranking.

#     Returns:
#         List of (score, passage), sorted by score descending.
#     """
#     # Prepare paired inputs: (query, passage)
#     pairs = [[query, passage] for passage in passages]

#     # Tokenize
#     inputs = tokenizer(pairs, padding=True, truncation=True, return_tensors="pt").to(device)
#     inputs = inputs.to(device)

#     # Get relevance scores (logits)
#     with torch.no_grad():
#         scores = model(**inputs).logits.squeeze(-1)

#     # Sort scores in descending order
#     sorted_indices = torch.argsort(scores, descending=True)
#     reranked = [(scores[i].item(), passages[i]) for i in sorted_indices[:top_k]]

#     return reranked

# query = "What are the benefits of using solar energy?"
# retrieved_docs = [
#     "Solar energy is renewable and sustainable.",
#     "Wind energy is another form of clean energy.",
#     "The sun produces energy through nuclear fusion.",
#     "Solar panels are used to capture sunlight.",
#     "Coal is a fossil fuel that causes pollution."
# ]

# reranked_docs = rerank(query, retrieved_docs, top_k=5)

# for score, doc in reranked_docs:
#     print(f"[{score:.4f}] {doc}")

# print (len(reranked_docs))
# '___________________________________________________________________________________________'
# import asyncio, time

# async def say_hello():
#     print("Hello")
#     await asyncio.sleep(0.0001)
#     print("World")

# async def main():
#     await asyncio.gather(say_hello(), say_hello())

# start = time.time()
# asyncio.run(main())
# print (time.time() - start)
# '___________________________________________________________________________________________'
# import threading

# def say_hello():
#     print("Hello")
#     time.sleep(0.0001)  # Simulates a delay
#     print("World")

# start = time.time()
# # Create the first thread
# thread1 = threading.Thread(target=say_hello) 
# # Create the second thread
# thread2 = threading.Thread(target=say_hello)  

# thread1.start()  # Start the first thread
# thread2.start()  # Start the second thread

# # Wait for the first thread to finish
# thread1.join()   
# # Wait for the second thread to finish
# thread2.join()
# print (time.time() - start)
# '___________________________________________________________________________________________'
# from elasticsearch import Elasticsearch, helpers

# client = Elasticsearch(
#     "https://my-elasticsearch-project-f493ea.es.us-central1.gcp.elastic.cloud:443",
#     api_key="RVJpTmM1Z0JzdEZnNGdrNzVNaHA6UDNwQktNeGR4TktfSk5xQzBvY3dzQQ=="
# )

# index_name = "f-prj"

# mappings = {
#     "properties": {
#         "text": {
#             "type": "semantic_text"
#         }
#     }
# }

# mapping_response = client.indices.put_mapping(index=index_name, body=mappings)
# print(mapping_response)

# docs = [
#     {
#         "text": "Yellowstone National Park is one of the largest national parks in the United States. It ranges from the Wyoming to Montana and Idaho, and contains an area of 2,219,791 acress across three different states. Its most famous for hosting the geyser Old Faithful and is centered on the Yellowstone Caldera, the largest super volcano on the American continent. Yellowstone is host to hundreds of species of animal, many of which are endangered or threatened. Most notably, it contains free-ranging herds of bison and elk, alongside bears, cougars and wolves. The national park receives over 4.5 million visitors annually and is a UNESCO World Heritage Site."
#     },
#     {
#         "text": "Yosemite National Park is a United States National Park, covering over 750,000 acres of land in California. A UNESCO World Heritage Site, the park is best known for its granite cliffs, waterfalls and giant sequoia trees. Yosemite hosts over four million visitors in most years, with a peak of five million visitors in 2016. The park is home to a diverse range of wildlife, including mule deer, black bears, and the endangered Sierra Nevada bighorn sheep. The park has 1,200 square miles of wilderness, and is a popular destination for rock climbers, with over 3,000 feet of vertical granite to climb. Its most famous and cliff is the El Capitan, a 3,000 feet monolith along its tallest face."
#     },
#     {
#         "text": "Rocky Mountain National Park  is one of the most popular national parks in the United States. It receives over 4.5 million visitors annually, and is known for its mountainous terrain, including Longs Peak, which is the highest peak in the park. The park is home to a variety of wildlife, including elk, mule deer, moose, and bighorn sheep. The park is also home to a variety of ecosystems, including montane, subalpine, and alpine tundra. The park is a popular destination for hiking, camping, and wildlife viewing, and is a UNESCO World Heritage Site."
#     }
# ]
# # Timeout to allow machine learning model loading and semantic ingestion to complete
# ingestion_timeout=300
# bulk_response = helpers.bulk(
#     client.options(request_timeout=ingestion_timeout),
#     docs,
#     index=index_name
# )
# print(bulk_response)
from elasticsearch import Elasticsearch

client = Elasticsearch(
    "https://my-elasticsearch-project-f493ea.es.us-central1.gcp.elastic.cloud:443",
    api_key="RVJpTmM1Z0JzdEZnNGdrNzVNaHA6UDNwQktNeGR4TktfSk5xQzBvY3dzQQ=="
)

retriever_object = {
    "standard": {
        "query": {
            "semantic": {
                "field": "text",
                "query": "REPLACE WITH YOUR QUERY"
            }
        }
    }
}

search_response = client.search(
    index="f-prj",
    retriever=retriever_object,
)
print(search_response['hits']['hits'])