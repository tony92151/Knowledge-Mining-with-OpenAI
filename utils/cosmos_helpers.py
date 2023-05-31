import copy
import json
import logging
import os
import re
import urllib
import uuid
from datetime import datetime, timedelta

import azure.functions as func
import numpy as np
from azure.cosmos import CosmosClient, PartitionKey

from utils import redis_helpers
from utils.env_vars import *

try:
    if DATABASE_MODE == 1:
        client = CosmosClient(url=COSMOS_URI, credential=COSMOS_KEY)
        partitionKeyPath = PartitionKey(path="/categoryId")
        database = client.create_database_if_not_exists(id=COSMOS_DB_NAME)

        def init_container():
            indexing_policy = {
                "includedPaths": [{"path": "/*"}],
                "excludedPaths": [
                    {"path": '/"_etag"/?'},
                    {"path": f"/{VECTOR_FIELD_IN_REDIS}/?"},
                ],
            }

            try:
                container = database.create_container_if_not_exists(
                    id="documents",
                    partition_key=partitionKeyPath,
                    indexing_policy=indexing_policy,
                )
            except:
                try:
                    container = database.create_container_if_not_exists(
                        id="documents",
                        partition_key=partitionKeyPath,
                        indexing_policy=indexing_policy,
                    )

                except Exception as e:
                    logging.error(f"Encountered error {e} while creating the container")
                    print(f"Encountered error {e} while creating the container")

            return container

        container = init_container()

except:
    print("Failed to initialize Cosmos DB container")
    logging.error("Failed to initialize Cosmos DB container")


def cosmos_restore_embeddings():
    QUERY = "SELECT * FROM documents p WHERE p.categoryId = @categoryId"
    params = [dict(name="@categoryId", value=EMBCATEGORYID)]

    embeddings = container.query_items(
        query=QUERY, parameters=params, enable_cross_partition_query=False
    )

    redis_conn = redis_helpers.get_new_conn()
    counter = 0

    try:
        for e in embeddings:
            counter += redis_helpers.redis_upsert_embedding(redis_conn, e)

    except Exception as e:
        print("No Documents found")

    logging.info(f"Loaded {counter} embeddings from Cosmos into Redis")
    print(f"Loaded {counter} embeddings from Cosmos into Redis")


def cosmos_backup_embeddings(emb_documents):
    ret_dict = {}

    try:
        for e in emb_documents:
            # e[VECTOR_FIELD_IN_REDIS] = np.array(e[VECTOR_FIELD_IN_REDIS]).astype(np.float32).tobytes()
            e["categoryId"] = EMBCATEGORYID
            container.upsert_item(e)

        ret_dict[
            "status"
        ] = f"Successfully loaded {len(emb_documents)} embedding documents into Cosmos"

    except Exception as e:
        logging.error(e)
        print(e)
        ret_dict[
            "status"
        ] = f"Failed loading {len(emb_documents)} embeddings into Cosmos: {e}"

    return ret_dict


def cosmos_store_contents(data_dict):
    ret_dict = {}

    new_doc = copy.deepcopy(data_dict)

    new_doc["id"] = new_doc.get("id", str(uuid.uuid4()))
    new_doc["categoryId"] = CATEGORYID
    new_doc["timestamp"] = (
        new_doc.get("timestamp", datetime.now().strftime("%m/%d/%Y, %H:%M:%S")),
    )
    new_doc["doc_url"] = new_doc.get(
        "doc_url", f"https://microsoft.com/{str(uuid.uuid4())}"
    )

    if "content" in new_doc.keys():
        del new_doc["content"]

    try:
        container.upsert_item(new_doc)
        ret_dict[
            "status"
        ] = f"Document {new_doc['id']} was successfully inserted into Cosmos"
    except Exception as e:
        logging.error(e)
        print(e)
        ret_dict[
            "status"
        ] = f"Document {new_doc['id']} failed to be inserted into Cosmos: {e}"

    return ret_dict


# def cosmos_download_contents():
#     QUERY = "SELECT * FROM documents p WHERE p.categoryId = @categoryId"
#     params = [dict(name="@categoryId", value=CATEGORYID)]

#     contents = container.query_items(query=QUERY, parameters=params, enable_cross_partition_query=False, max_item_count=10)
#     counter = 0

#     try:
#         for c in contents:
#             #counter += redis_helpers.redis_upsert_embedding(redis_conn, e)
#             # print(c)
#             yield self._parse_entry(item_dict)

#     except Exception as e:
#         print("No Documents found")

#     logging.info(f"Loaded {counter} embeddings from Cosmos into Redis")
#     print(f"Loaded {counter} embeddings from Cosmos into Redis")
