import logging
import os
import re
import time

import numpy as np
import openai
import tiktoken
from langchain.prompts.chat import (ChatPromptTemplate,
                                    HumanMessagePromptTemplate,
                                    MessagesPlaceholder,
                                    SystemMessagePromptTemplate)

import utils.langchain_helpers.simple_prompt
from utils import helpers, openai_helpers, redis_helpers
from utils.env_vars import *

system_message = "The assistant is a super helpful assistant that plays the role of a linguistic professor and has ultra high attention to details."

instruction = """From the above Question and Current Conversation, output search keywords to use in a search engine to get an answer for the Question. If the Question is not related to the Current Conversation, then do not use the Current Conversation when generating the Search Keywords.
Search Keywords:"""

body = """
Current Conversation: 
{history}

Question: {question}
"""

context_prompt = """
<|im_start|>
{system_message}
<|im_end|>
<|im_start|>user 

Current Conversation: 
{history}

Question: {question}

{instruction}
<|im_end|>
<|im_start|>assistant
"""


class OldSchoolSearch:
    def search(
        self,
        query,
        history,
        pre_context,
        filter_param=None,
        enable_unified_search=False,
        lc_agent=None,
        enable_cognitive_search=False,
        evaluate_step=True,
        topK=NUM_TOP_MATCHES,
        stream=False,
        verbose=False,
    ):
        redis_conn = redis_helpers.get_new_conn()

        completion_model = CHOSEN_COMP_MODEL
        embedding_model = CHOSEN_EMB_MODEL
        completion_enc = openai_helpers.get_encoder(completion_model)
        embedding_enc = openai_helpers.get_encoder(embedding_model)

        if verbose:
            print("Old Query: ", query)
        gen = openai_helpers.get_generation(completion_model)

        if history != "":
            if (gen == 4) or (gen == 3.5):
                messages = [
                    SystemMessagePromptTemplate.from_template(system_message).format(),
                    HumanMessagePromptTemplate.from_template(body).format(
                        history=history, question=query
                    ),
                    HumanMessagePromptTemplate.from_template(instruction).format(),
                ]
                messages = openai_helpers.convert_messages_to_roles(messages)
                query = openai_helpers.contact_openai(messages)
            else:
                prompt = context_prompt.format(
                    system_message=system_message,
                    history=history,
                    question=query,
                    instruction=instruction,
                )
                query = openai_helpers.contact_openai(prompt)

        if (gen == 4) or (gen == 3.5):
            p = ""
            for m in utils.langchain_helpers.simple_prompt.get_simple_prompt(
                "", "", "", ""
            ):
                p += m["content"]
            empty_prompt_length = len(completion_enc.encode(p))
        else:
            empty_prompt_length = len(
                completion_enc.encode(
                    utils.langchain_helpers.simple_prompt.get_simple_prompt(
                        "", "", "", ""
                    )
                )
            )

        if verbose:
            print("New Query: ", query)

        max_comp_model_tokens = openai_helpers.get_model_max_tokens(completion_model)
        max_emb_model_tokens = openai_helpers.get_model_max_tokens(embedding_model)

        if lc_agent.enable_unified_search:
            context = lc_agent.unified_search(query)
        elif enable_cognitive_search:
            context = lc_agent.agent_cog_search(query)
        # elif lc_agent.use_bing:
        #     context = lc_agent.agent_bing_search(query)
        else:
            context = lc_agent.agent_redis_search(query)

        query = completion_enc.decode(completion_enc.encode(query)[:MAX_QUERY_TOKENS])
        history = completion_enc.decode(
            completion_enc.encode(history)[:MAX_HISTORY_TOKENS]
        )
        pre_context = completion_enc.decode(
            completion_enc.encode(pre_context)[:PRE_CONTEXT]
        )

        context_length = len(completion_enc.encode(context))
        query_length = len(completion_enc.encode(query))
        history_length = len(completion_enc.encode(history))
        pre_context_length = len(completion_enc.encode(pre_context))

        max_context_len = (
            max_comp_model_tokens
            - query_length
            - MAX_OUTPUT_TOKENS
            - empty_prompt_length
            - history_length
            - pre_context_length
            - 1
        )

        context = completion_enc.decode(
            completion_enc.encode(context)[:max_context_len]
        )

        prompt = utils.langchain_helpers.simple_prompt.get_simple_prompt(
            context, query, history, pre_context
        )

        if verbose:
            print("$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$")
            print(prompt)
            print("$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$")

        if verbose:
            print("OSS OAI Call")
        answer = openai_helpers.contact_openai(
            prompt, completion_model, MAX_OUTPUT_TOKENS, stream=stream, verbose=verbose
        )

        return answer
