import copy
import json
import logging
import os
import pickle
import urllib
import uuid
from datetime import datetime, timedelta

import numpy as np
import smart_open
from azure.storage.blob import (BlobClient, BlobSasPermissions,
                                BlobServiceClient, ContainerClient,
                                __version__, generate_blob_sas)
from requests.utils import requote_uri

from utils.env_vars import *


def get_kb_container_client():
    blob_service_client = BlobServiceClient.from_connection_string(KB_BLOB_CONN_STR)
    return blob_service_client


blob_service_client = get_kb_container_client()


def get_container_name(url):
    return url.split(".blob.core.windows.net/")[1].split("/")[0]


def create_sas_from_container_and_blob(container, blob_name):
    blob_client = blob_service_client.get_blob_client(
        container=container, blob=blob_name
    )

    token = generate_blob_sas(
        account_name=blob_client.account_name,
        account_key=blob_client.credential.account_key,
        container_name=container,
        blob_name=blob_name,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=20 * 365 * 24),
    )

    sas_url = blob_client.url + "?" + token
    # print(f"Processing now '{blob_name}' with SAS URL {sas_url}")
    return sas_url


def get_filename(blob_path):
    try:
        return urllib.parse.unquote(os.path.basename(blob_path.split("?")[0]))
    except:
        return "default_file_name_exception"


def create_sas(blob_path):
    blob_name = get_filename(blob_path)
    container = get_container_name(blob_path)
    return create_sas_from_container_and_blob(container, blob_name)


def save_json_document(data_dict, container=OUTPUT_BLOB_CONTAINER):
    ret_dict = {}

    new_doc = copy.deepcopy(data_dict)

    new_doc["id"] = new_doc.get("id", str(uuid.uuid4()))
    new_doc["categoryId"] = CATEGORYID
    new_doc["timestamp"] = new_doc.get(
        "timestamp", datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
    )
    new_doc["doc_url"] = new_doc.get(
        "doc_url", f"https://microsoft.com/{str(uuid.uuid4())}"
    )

    if "content" in new_doc.keys():
        del new_doc["content"]

    container_client = blob_service_client.get_container_client(container)

    try:
        container_properties = container_client.get_container_properties()
    except Exception as e:
        container_client.create_container()

    blob_name = urllib.parse.unquote(os.path.basename(new_doc["doc_url"].split("?")[0]))
    pre, ext = os.path.splitext(blob_name)
    blob_name = pre + ".json"
    blob_client = container_client.get_blob_client(blob=blob_name)
    blob_client.upload_blob(json.dumps(new_doc, indent=4), overwrite=True)
    ret_dict[
        "status"
    ] = f"Document {new_doc['id']} was successfully saved to the {OUTPUT_BLOB_CONTAINER} container"
    logging.info(ret_dict["status"])

    return ret_dict


def list_documents(container):
    container_client = blob_service_client.get_container_client(container)
    generator = container_client.list_blobs()
    blobs = []
    for blob in generator:
        blob_client = blob_service_client.get_blob_client(
            container=container, blob=blob.name
        )
        blobs.append(blob_client.url)

    return blobs


def get_document_url(container, filename):
    url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{container}/{filename}"
    return requote_uri(url)


def get_document(container, filename):
    transport_params = {"client": blob_service_client}

    with smart_open.open(
        f"azure://{container}/{filename}", transport_params=transport_params
    ) as fin:
        data = fin.read()

    return data


def download_document(url, as_text=True):
    blob_client = blob_service_client.get_blob_client(
        container=container, blob=blob_name
    )
    blob_name = urllib.parse.unquote(os.path.basename(blob_path))
    container = get_container_name(blob_path)
    download_stream = blob_client.download_blob()

    if as_text:
        return download_stream.content_as_text()
    else:
        return download_stream.content_as_bytes()
