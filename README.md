# US LAW ADIVISORY

## I - Introduction


## II - Setup 
1. Setup docker
    ```bash
    docker compose up -d --build
    ```

2. Run demo
    ```
    docker ps 
    # Get the la-server id
    docker exec -it <la-server id> bash
    python -m test.all_demo # Run all demos
    python -m demo.elastic # Run elasticsearch demo
    python -m demo.mongodb # Run mongodb demo
    python -m demo.qdrant # Run qdrant demo
    ```




## Feature
- Allow user to open multiples chat at one
- Allow MemorySaver
- UX -> tao 1 admin de response lai feedback cua user if they choose node `contact/complain`
- setup network(docker)
- code rest_api

## Data
- research US law types
- data legit (prioritize taking text documents)
- Why response to this question "If I commit a crime (steal property), how long will I be sentenced?" is all about Califonia