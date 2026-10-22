import os
import json
from datetime import datetime
import app.agents.app_agents_config as app_agents_config
from app.utils.logger import sys_logger
from typing import List
# from langchain_core.messages import AIMessage
from app.models.schemas import AnalyzerOutput

import litellm

litellm.suppress_debug_info = app_agents_config.litellm_suppress_debug_info_flag

# 初始化基础日志路径
LOG_DIR = app_agents_config.llm_log_dir
os.makedirs(LOG_DIR, exist_ok=True)


class LLMClient:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        # 生命周期绑定
        # 在 client 实例化时，立刻生成一个全局的、带有时间戳的 .jsonl 追踪文件。
        # 只要这个程序在跑，所有的 Agent 调用记录都会往这一个文件里灌。
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.trace_filename = f"{LOG_DIR}/llm_trace_run_{run_timestamp}.jsonl"

    def _write_debug_trace(self, agent_role: str, trace_data: dict, **kwargs):

        # 统一外层数据骨架
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_role,
            "metadata": kwargs,
            "trace_data": trace_data
        }
        with open(self.trace_filename, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    def sample_reader01(self, segment_num: int, system_prompt: str, user_prompt: str, response_model):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        openrouter_extra_params = {
            "provider": {
                "order": app_agents_config.sample_reader01_model_provider_order,
                "allow_fallbacks": False
            }
        }

        trace_data = {
            "model": app_agents_config.sample_reader01_model,
            "token_usage": {},
            "status": "INIT",
            "error_message": None
        }

        try:
            sys_logger.info(f"调用llm{app_agents_config.sample_reader01_model}")
            response = litellm.completion(
                model=app_agents_config.sample_reader01_model,
                messages=messages,
                api_key=self.api_key,
                response_format=response_model,
                extra_body=openrouter_extra_params
            )

            sys_logger.info("获得response")

            # 将返回的文本直接反序列化为 Pydantic 真值对象
            content = response.choices[0].message.content

            trace_data["output_raw"] = content
            trace_data["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cached_tokens": response.usage.prompt_tokens_details.cached_tokens,
                "reasoning_tokens": response.usage.completion_tokens_details.reasoning_tokens
            }

            sys_logger.info(
                f"[LLM Response Captured] Seg {segment_num} | Tokens: In {trace_data['token_usage']['prompt_tokens']} / Out {trace_data['token_usage']['completion_tokens']}")

            # 字符串反序列化，并逐个字段检查.一旦发现有脏数据，立刻抛出 ValidationError 拦截
            validated_payload = response_model.model_validate_json(content)

            trace_data["status"] = "SUCCESS"
            return validated_payload

        except Exception as e:
            trace_data["status"] = "ERROR"
            trace_data["error_message"] = str(e)
            sys_logger.error(f"[LLM Error Exception] Seg {segment_num} 发生崩溃! 详情查看 Trace 日志。错误原因: {str(e)}")
            raise e

        finally:
            self._write_debug_trace(
                agent_role="reader01",
                trace_data=trace_data,
                segment_num=segment_num  # 动态打上的业务标签
            )

    def sample_reader01_macro_summarizer(self, macro_name: str, system_prompt: str, user_prompt: str, response_model):

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        openrouter_extra_params = {
            "provider": {
                "order": app_agents_config.sample_reader01_summarizer_model_provider_order,
                "allow_fallbacks": False
            }
        }

        trace_data = {
            "model": app_agents_config.sample_reader01_summarizer_model,
            "token_usage": {},
            "status": "INIT",
            "error_message": None
        }

        try:
            sys_logger.info(f"[Macro Summarizer] 请求大模型归纳节点 [{macro_name}] ...")
            response = litellm.completion(
                model=app_agents_config.sample_reader01_summarizer_model,
                messages=messages,
                api_key=self.api_key,
                response_format=response_model,
                extra_body=openrouter_extra_params
            )

            content = response.choices[0].message.content
            trace_data["output_raw"] = content
            trace_data["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }

            sys_logger.info(
                f"[[Macro Summarizer] Response Captured] {macro_name} | Tokens: In {trace_data['token_usage']['prompt_tokens']} / Out {trace_data['token_usage']['completion_tokens']}")

            # Pydantic 强校验，不合规直接扔进 except 走重试
            validated_payload = response_model.model_validate_json(content)
            trace_data["status"] = "SUCCESS"
            return validated_payload

        except Exception as e:
            trace_data["status"] = "ERROR"
            trace_data["error_message"] = str(e)
            sys_logger.error(f"[Macro Summarizer Error] {macro_name} Trace: {str(e)}")
            raise e

        finally:

            self._write_debug_trace(
                agent_role="reader01_macro_summarizer",
                trace_data=trace_data,
                macro_name=macro_name
            )

    def sample_reader02(self, segment_num: int, system_prompt: str, user_prompt: str, response_model, agent_role: str):

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        openrouter_extra_params = {
            "provider": {
                "order": app_agents_config.sample_reader02_model_provider_order,
                "allow_fallbacks": False
            }
        }

        trace_data = {
            "model": app_agents_config.sample_reader02_model,
            "token_usage": {},
            "status": "INIT",
            "error_message": None
        }

        try:
            sys_logger.info(f"[{agent_role}] 开始调用 LLM...")
            response = litellm.completion(
                model=app_agents_config.sample_reader02_model,
                messages=messages,
                api_key=self.api_key,
                response_format=response_model,
                extra_body=openrouter_extra_params
            )

            content = response.choices[0].message.content

            trace_data["output_raw"] = content
            trace_data["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cached_tokens": response.usage.prompt_tokens_details.cached_tokens,
                "reasoning_tokens": response.usage.completion_tokens_details.reasoning_tokens
            }
            sys_logger.info(
                f"[{agent_role} LLM Response Captured] Seg {segment_num} | Tokens: In {trace_data['token_usage']['prompt_tokens']} / Out {trace_data['token_usage']['completion_tokens']}")

            validated_payload = response_model.model_validate_json(content)
            trace_data["status"] = "SUCCESS"
            return validated_payload

        except Exception as e:
            trace_data["status"] = "ERROR"
            trace_data["error_message"] = str(e)
            sys_logger.error(f"[{agent_role} LLM Error] Seg {segment_num} Trace: {str(e)}")
            raise e
        finally:
            self._write_debug_trace(
                agent_role=agent_role,
                trace_data=trace_data,
                segment_num=segment_num
            )

    def sample_reader03(self, segment_num: int, system_prompt: str, user_prompt: str, response_model, agent_role: str):

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        openrouter_extra_params = {
            "provider": {
                "order": app_agents_config.sample_reader03_model_provider_order,
                "allow_fallbacks": False
            }
        }

        trace_data = {
            "model": app_agents_config.sample_reader03_model,
            "token_usage": {},
            "status": "INIT",
            "error_message": None
        }

        try:
            sys_logger.info(f"[{agent_role}] 开始调用 LLM...")
            response = litellm.completion(
                model=app_agents_config.sample_reader03_model,
                messages=messages,
                api_key=self.api_key,
                response_format=response_model,
                extra_body=openrouter_extra_params
            )

            content = response.choices[0].message.content

            trace_data["output_raw"] = content
            trace_data["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cached_tokens": response.usage.prompt_tokens_details.cached_tokens,
                "reasoning_tokens": response.usage.completion_tokens_details.reasoning_tokens
            }
            sys_logger.info(
                f"[{agent_role} LLM Response Captured] Seg {segment_num} | Tokens: In {trace_data['token_usage']['prompt_tokens']} / Out {trace_data['token_usage']['completion_tokens']}")

            validated_payload = response_model.model_validate_json(content)
            trace_data["status"] = "SUCCESS"
            return validated_payload

        except Exception as e:
            trace_data["status"] = "ERROR"
            trace_data["error_message"] = str(e)
            sys_logger.error(f"[{agent_role} LLM Error] Seg {segment_num} Trace: {str(e)}")
            raise e
        finally:
            self._write_debug_trace(
                agent_role=agent_role,
                trace_data=trace_data,
                segment_num=segment_num
            )

    def get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:

        if not texts:
            return []

        embed_model = app_agents_config.embedding_reader03_model

        try:
            response = litellm.embedding(
                model=embed_model,
                input=texts,
                api_key=self.api_key
            )
            return [
                item["embedding"] if isinstance(item, dict) else item.embedding
                for item in response.data
            ]
        except Exception as e:
            sys_logger.error(f"LLMClient 生成 Embedding 失败: {e}")
            raise

    def sample_analyzer(self, messages: list, system_prompt: str) -> AnalyzerOutput:

        formatted_messages = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            role = "user" if msg.type == "human" else ("assistant" if msg.type == "ai" else msg.type)
            if msg.type in ["human", "ai"] and getattr(msg, "content", ""):
                formatted_messages.append({"role": role, "content": msg.content})

        openrouter_extra_params = {
            "provider": {
                "order": app_agents_config.sample_analyzer_model_provider_order,
                "allow_fallbacks": False
            }
        }

        trace_data = {
            "model": app_agents_config.sample_analyzer_model,
            "token_usage": {},
            "status": "INIT",
            "error_message": None
        }

        try:
            sys_logger.info("[Analyzer] 发起 LLM 通讯...")
            response = litellm.completion(
                model=app_agents_config.sample_analyzer_model,
                messages=formatted_messages,
                api_key=self.api_key,
                response_format=AnalyzerOutput,
                extra_body=openrouter_extra_params
            )

            content = response.choices[0].message.content
            trace_data["output_raw"] = content

            trace_data["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }

            validated_payload = AnalyzerOutput.model_validate_json(content)
            trace_data["status"] = "SUCCESS"
            return validated_payload

        except Exception as e:
            trace_data["status"] = "ERROR"
            trace_data["error_message"] = str(e)
            sys_logger.error(f"[Analyzer] LLM 异常: {e}")
            raise e
        finally:
            self._write_debug_trace(
                agent_role="Analyzer",
                trace_data=trace_data
            )
