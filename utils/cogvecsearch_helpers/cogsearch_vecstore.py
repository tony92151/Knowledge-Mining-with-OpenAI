import copy
import json
import logging
import os
import re
import uuid

import utils.cogvecsearch_helpers.cs_json
from utils import cv_helpers, helpers, http_helpers, kb_doc, openai_helpers
from utils.env_vars import *


class CogSearchVecStore:
    def __init__(
        self,
        api_key=COG_SEARCH_ADMIN_KEY,
        search_service_name=COG_SEARCH_ENDPOINT,
        index_name=COG_VECSEARCH_VECTOR_INDEX,
        api_version=COG_VEC_SEARCH_API_VERSION,
        load_addtl_fields=True,
    ):
        self.http_req = http_helpers.CogSearchHttpRequest(
            api_key, search_service_name, index_name, api_version
        )
        self.index_name = index_name
        self.all_fields = ["id", "text", "text_en", "categoryId"]
        self.search_types = ["vector", "hybrid", "semantic_hybrid"]

        self.addtl_fields = []

        if load_addtl_fields:
            self.addtl_fields += list(
                kb_doc.KB_Doc().get_fields()
                - [
                    "text",
                    "text_en",
                    VECTOR_FIELD_IN_REDIS,
                    "id",
                    "cv_image_vector",
                    "cv_text_vector",
                ]
            )
            self.all_fields += self.addtl_fields

    def create_index(self):
        index_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.create_index_json)
        index_dict["name"] = self.index_name

        for f in self.addtl_fields:
            field_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.field_json)
            field_dict["name"] = f
            index_dict["fields"].append(field_dict)

        self.http_req.put(body=index_dict)

    def get_index(self):
        return self.http_req.get()

    def delete_index(self):
        return self.http_req.delete()

    def upload_documents(self, documents):
        docs_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.upload_docs_json)

        for doc in documents:
            doc_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.upload_doc_json)

            for k in self.all_fields:
                doc_dict[k] = doc.get(k, "")

            doc_dict["id"] = doc["id"] if doc.get("id", None) else str(uuid.uuid4())
            doc_dict[VECTOR_FIELD_IN_REDIS] = doc.get(VECTOR_FIELD_IN_REDIS, [])
            doc_dict["cv_image_vector"] = doc.get("cv_image_vector", [])
            doc_dict["cv_text_vector"] = doc.get("cv_text_vector", [])
            doc_dict["@search.action"] = "upload"
            docs_dict["value"].append(doc_dict)

        self.http_req.post(op="index", body=docs_dict)

        return docs_dict

    def delete_documents(self, op="index", ids=[]):
        docs_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.upload_docs_json)

        for i in ids:
            doc_dict = copy.deepcopy(utils.cogvecsearch_helpers.cs_json.upload_doc_json)
            doc_dict["id"] = i
            doc_dict[VECTOR_FIELD_IN_REDIS] = [0] * openai_helpers.get_model_dims(
                CHOSEN_EMB_MODEL
            )
            doc_dict["@search.action"] = "delete"
            docs_dict["value"].append(doc_dict)

        self.http_req.post(op="index", body=docs_dict)

    def get_search_json(self, query, search_type="vector"):
        if search_type == "vector":
            query_dict = copy.deepcopy(
                utils.cogvecsearch_helpers.cs_json.search_dict_vector
            )
        elif search_type == "hybrid":
            query_dict = copy.deepcopy(
                utils.cogvecsearch_helpers.cs_json.search_dict_hybrid
            )
            query_dict["search"] = query
        elif search_type == "semantic_hybrid":
            query_dict = copy.deepcopy(
                utils.cogvecsearch_helpers.cs_json.search_dict_semantic_hybrid
            )
            query_dict["search"] = query
        return query_dict

    def get_vector_fields(self, query, query_dict, vector_name=None):
        if (vector_name is None) or (vector_name == VECTOR_FIELD_IN_REDIS):
            completion_enc = openai_helpers.get_encoder(CHOSEN_COMP_MODEL)
            embedding_enc = openai_helpers.get_encoder(CHOSEN_EMB_MODEL)
            query_dict["vector"]["fields"] = VECTOR_FIELD_IN_REDIS
            query = embedding_enc.decode(embedding_enc.encode(query)[:MAX_QUERY_TOKENS])
            query_dict["vector"]["value"] = openai_helpers.get_openai_embedding(
                query, CHOSEN_EMB_MODEL
            )
        elif vector_name == "cv_text_vector":
            cvr = cv_helpers.CV()
            query_dict["vector"]["fields"] = vector_name
            query_dict["vector"]["value"] = cvr.get_text_embedding(query)
        elif vector_name == "cv_image_vector":
            cvr = cv_helpers.CV()
            query_dict["vector"]["fields"] = vector_name
            query_dict["vector"]["value"] = cvr.get_img_embedding(query)
        else:
            raise Exception(f"Invalid Vector Name {vector_name}")

        return query_dict

    def search(
        self,
        query,
        search_type="vector",
        vector_name=None,
        select=None,
        filter=None,
        verbose=False,
    ):
        if search_type not in self.search_types:
            raise Exception(f"search_type must be one of {self.search_types}")

        regex = r"(https?:\/\/[^\/\s]+(?:\/[^\/\s]+)*\/[^?\/\s]+(?:\.jpg|\.jpeg|\.png)(?:\?[^\s'\"]+)?)"
        match = re.search(regex, query)

        if match:
            sas_url = match.group(1)
            cvr = cv_helpers.CV()
            res = cvr.analyze_image(img_url=sas_url)
            query = query.replace(sas_url, "") + "\n" + res["text"]

        query_dict = self.get_search_json(query, search_type)
        query_dict = self.get_vector_fields(query, query_dict, vector_name)
        query_dict["vector"]["k"] = NUM_TOP_MATCHES
        query_dict["filter"] = filter
        query_dict["select"] = ", ".join(self.all_fields) if select is None else select

        results = self.http_req.post(op="search", body=query_dict)
        results = results["value"]
        if verbose:
            [print(r["@search.score"]) for r in results]

        if match:
            sas_url = match.group(1)
            query_dict = self.get_vector_fields(sas_url, query_dict, "cv_image_vector")
            img_results = self.http_req.post(op="search", body=query_dict)
            results = [img_results["value"], results]

            max_items = max([len(r) for r in results])

            final_context = []
            context_dict = {}

            for i in range(max_items):
                for j in range(len(results)):
                    if i < len(results[j]):
                        if results[j][i]["id"] not in context_dict:
                            context_dict[results[j][i]["id"]] = 1
                            final_context.append(results[j][i])

            results = final_context

        context = helpers.process_search_results(results)

        if match:
            return [
                "Analysis of the image in the question: " + query + "\n\n"
            ] + context
        else:
            return context

    def search_similar_images(self, query, select=None, filter=None, verbose=False):
        search_type = "vector"
        vector_name = "cv_image_vector"

        if search_type not in self.search_types:
            raise Exception(f"search_type must be one of {self.search_types}")

        regex = r"(https?:\/\/[^\/\s]+(?:\/[^\/\s]+)*\/[^?\/\s]+(?:\.jpg|\.jpeg|\.png)(?:\?[^\s'\"]+)?)"
        match = re.search(regex, query)

        if match:
            url = match.group(1)
            query_dict = self.get_search_json(url, search_type)
            query_dict = self.get_vector_fields(url, query_dict, vector_name)
            query_dict["vector"]["k"] = NUM_TOP_MATCHES
            query_dict["filter"] = filter
            query_dict["select"] = (
                ", ".join(self.all_fields) if select is None else select
            )

            results = self.http_req.post(op="search", body=query_dict)
            results = results["value"]
            if verbose:
                [print(r["@search.score"]) for r in results]

            context = helpers.process_search_results(results)

            return context

        else:
            return ["Sorry, no similar images have been found"]
