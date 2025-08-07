from elasticsearch import Elasticsearch, helpers
import json
import os
import pickle
from datetime import datetime
from typing import List, Dict, Any, Optional

class ElasticsearchManager:
    def __init__(self):
        self.host = os.getenv("ELASTIC_HOST", "http://la-elasticsearch-service:9200")
        self.password = os.getenv("ELASTIC_PASSWORD")
        self.api_key = os.getenv("ELASTIC_API_KEY")
        
        self.client = Elasticsearch(
            [self.host],
            basic_auth=("elastic", self.password),
            verify_certs=False,
            ssl_show_warn=False
        )
        
        self.index_name = "lawadvisory-f_prj"
        self.data_dir = "elasticsearch_data"
        
        # Create data directory if it doesn't exist
        os.makedirs(self.data_dir, exist_ok=True)
    
    def backup_index_data(self, backup_name: Optional[str] = None) -> str:
        """
        Backup all data from the index to local files
        Returns the backup filename
        """
        try:
            if not backup_name:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"backup_{self.index_name}_{timestamp}"
            
            print(f"💾 Creating backup: {backup_name}")
            
            # Get all documents
            search_response = self.client.search(
                index=self.index_name,
                body={
                    "query": {"match_all": {}},
                    "size": 10000  # Adjust for large datasets
                }
            )
            
            documents = []
            for hit in search_response['hits']['hits']:
                doc_data = {
                    "id": hit['_id'],
                    "source": hit['_source']
                }
                documents.append(doc_data)
            
            # Save as JSON
            json_filename = os.path.join(self.data_dir, f"{backup_name}.json")
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump({
                    "index_name": self.index_name,
                    "backup_date": datetime.now().isoformat(),
                    "document_count": len(documents),
                    "documents": documents
                }, f, indent=2, ensure_ascii=False)
            
            # Save as pickle for faster loading
            pickle_filename = os.path.join(self.data_dir, f"{backup_name}.pkl")
            with open(pickle_filename, 'wb') as f:
                pickle.dump({
                    "index_name": self.index_name,
                    "backup_date": datetime.now().isoformat(),
                    "document_count": len(documents),
                    "documents": documents
                }, f)
            
            print(f"✅ Backup created successfully:")
            print(f"   JSON: {json_filename}")
            print(f"   Pickle: {pickle_filename}")
            print(f"   Documents backed up: {len(documents)}")
            
            return backup_name
            
        except Exception as e:
            print(f"❌ Error creating backup: {e}")
            return ""
    
    def restore_from_backup(self, backup_name: str, overwrite: bool = False) -> bool:
        """
        Restore data from a backup file
        If overwrite=True, it will clear existing data first
        """
        try:
            pickle_filename = os.path.join(self.data_dir, f"{backup_name}.pkl")
            json_filename = os.path.join(self.data_dir, f"{backup_name}.json")
            
            # Try pickle first (faster), then JSON
            backup_data = None
            if os.path.exists(pickle_filename):
                print(f"📥 Loading from pickle backup: {pickle_filename}")
                with open(pickle_filename, 'rb') as f:
                    backup_data = pickle.load(f)
            elif os.path.exists(json_filename):
                print(f"📥 Loading from JSON backup: {json_filename}")
                with open(json_filename, 'r', encoding='utf-8') as f:
                    backup_data = json.load(f)
            else:
                print(f"❌ Backup file not found: {backup_name}")
                return False
            
            documents = backup_data['documents']
            print(f"📊 Backup contains {len(documents)} documents from {backup_data['backup_date']}")
            
            # Check if we should overwrite existing data
            current_count = self.client.count(index=self.index_name)['count']
            if current_count > 0:
                if overwrite:
                    print("🗑️  Clearing existing data...")
                    self.remove_all_documents()
                else:
                    print(f"⚠️  Index contains {current_count} documents. Use overwrite=True to replace them.")
                    return False
            
            # Prepare documents for bulk insert
            bulk_docs = []
            for doc in documents:
                bulk_doc = {
                    "_index": self.index_name,
                    "_id": doc["id"],
                    "_source": doc["source"]
                }
                bulk_docs.append(bulk_doc)
            
            # Bulk insert
            print(f"📤 Restoring {len(bulk_docs)} documents...")
            bulk_response = helpers.bulk(
                self.client.options(request_timeout=300),
                bulk_docs
            )
            
            print(f"✅ Restore completed: {bulk_response[0]} documents indexed")
            
            # Refresh index
            self.client.indices.refresh(index=self.index_name)
            
            # Verify
            final_count = self.client.count(index=self.index_name)['count']
            print(f"📊 Final document count: {final_count}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error restoring from backup: {e}")
            return False
    
    def list_backups(self) -> List[str]:
        """List all available backups"""
        try:
            backups = []
            for filename in os.listdir(self.data_dir):
                if filename.endswith('.pkl') or filename.endswith('.json'):
                    backup_name = filename.rsplit('.', 1)[0]
                    if backup_name not in backups:
                        backups.append(backup_name)
            
            print(f"📋 Available backups ({len(backups)}):")
            for i, backup in enumerate(backups, 1):
                # Try to get backup info
                pickle_file = os.path.join(self.data_dir, f"{backup}.pkl")
                json_file = os.path.join(self.data_dir, f"{backup}.json")
                
                size_info = ""
                if os.path.exists(pickle_file):
                    size_mb = os.path.getsize(pickle_file) / (1024 * 1024)
                    size_info = f" ({size_mb:.2f} MB)"
                elif os.path.exists(json_file):
                    size_mb = os.path.getsize(json_file) / (1024 * 1024)
                    size_info = f" ({size_mb:.2f} MB)"
                
                print(f"  {i}. {backup}{size_info}")
            
            return backups
            
        except Exception as e:
            print(f"❌ Error listing backups: {e}")
            return []
    
    def get_document_count(self) -> int:
        """Get current document count in the index"""
        try:
            count = self.client.count(index=self.index_name)['count']
            print(f"📊 Current document count: {count}")
            return count
        except Exception as e:
            print(f"❌ Error getting document count: {e}")
            return 0

# Example usage and helper functions
def interactive_document_removal():
    """Interactive function to choose and remove documents"""
    manager = ElasticsearchManager()
    
    while True:
        print("\n" + "="*50)
        print("🗑️  DOCUMENT REMOVAL MENU")
        print("="*50)
        print("1. Remove ALL documents")
        print("2. Remove specific document")
        print("3. List all document IDs")
        print("4. Show document count")
        print("5. Quit")
        
        choice = input("\nChoose an option (1-5): ").strip()
        
        if choice == '1':
            confirm = input("⚠️  Are you sure you want to remove ALL documents? (yes/no): ")
            if confirm.lower() == 'yes':
                manager.remove_all_documents()
            else:
                print("Operation cancelled.")
                
        elif choice == '2':
            doc_ids = manager.list_all_document_ids()
            if doc_ids:
                doc_id = input("\nEnter document ID to remove: ").strip()
                if doc_id in doc_ids:
                    manager.remove_specific_document(doc_id)
                else:
                    print("Invalid document ID")
            else:
                print("No documents found")
                
        elif choice == '3':
            manager.list_all_document_ids()
            
        elif choice == '4':
            manager.get_document_count()
            
        elif choice == '5':
            break
            
        else:
            print("Invalid choice")

# Example usage
if __name__ == "__main__":
    # manager = ElasticsearchManager()
    
    # print("🔧 ELASTICSEARCH DOCUMENT MANAGER")
    # print("Connected to:", manager.host)
    
    # # Show current status
    # manager.remove_all_documents()
    
    # # Example operations (uncomment what you need):
    
    # # Create a backup before making changes
    # # backup_name = manager.backup_index_data()
    
    # # List all backups
    # # manager.list_backups()
    
    # # Interactive removal
    # # interactive_document_removal()
    
    # # Restore from backup
    # # manager.restore_from_backup("backup_name_here", overwrite=True)
    
    # print("\n✅ Manager ready for use!")
    meh = "c ted for discharging industrial refuse into a river, in violation of 13 of the Rivers and Harbors Act of 1899 In its regulations promulgated under the Act, the Army Corps of Engineers had consistently construed 13 as limited to discharges that affected navigation PICCO's discharge was such that it would not affect navigation Relying on Raley and Cox, the Court reversed the conviction, finding [t"
    print (len(meh))