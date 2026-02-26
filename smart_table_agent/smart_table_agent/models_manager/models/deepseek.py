import os
from abc import ABC
import json
from openai import OpenAI
from .llm_base import LLMBase
from ..function_manager import MyFunctions

__all__ = ["DeepSeek"]


class DeepSeek(LLMBase, ABC):
    base_url = "https://api.deepseek.com"
    function_call = MyFunctions()

    def __init__(self, model_name=None, api_key=None):
        if api_key is None:
            api_key = os.environ.get("DEEPSEEK_API_KEY", None)
        if model_name is None:
            model_name = "deepseek-chat"
        super().__init__(model_name, api_key)
        self.client = OpenAI(api_key=self._api_key, base_url=self.base_url)
        # self.model_name="deepseek-reasoner",

    def single_request(self, user_input, stream=False, stream_callback=None, tools=None):
        """
        模型单次请求
        :param user_input:
        :param stream: stream=True的时候，启用流示返回
        :param stream_callback:
        :param tools:
        :return:
        """
        self.chat_history = [{"role": "user", "content": user_input}]
        response = self._send_request(self.chat_history, stream=stream, tools=tools)
        response_content = self._response_handle(response, stream=stream, stream_callback=stream_callback, tools=tools)
        return response_content

    def multiple_requests(self, user_input_info: str = "", restart: bool = False, stream: bool = False,
                          stream_callback=None, tools=None):
        """
        多轮聊天信息请求
        :param user_input_info:
        :param restart: 是否重新开启会话
        :param stream:
        :param stream_callback:
        :param tools:
        :return:
        """
        if restart:
            self.reset()
            self.chat_history = [{"role": "user", "content": user_input_info}]
        else:
            self.chat_history.append({"role": "user", "content": user_input_info})
        response = self._send_request(self.chat_history, stream=stream, tools=tools)
        response_content = self._response_handle(response, stream=stream, stream_callback=stream_callback, tools=tools)
        return response_content

    def _send_request(self, messages: list[dict], stream=False, tools=None):
        """
        发送请求信息
        :param messages: 聊天信息
        :param stream: 是否开启流式输出
        :param tools: 工具
        :return:
        """
        request_times = 0
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self._model_name,
                    messages=messages,
                    stream=stream,
                    tools=tools,
                )
                return response
            except Exception as e:
                print(e)
            request_times += 1
            if request_times >= 4:
                break
        return {"role": "assistant", "content": "不好意思，出错了。请重新请求。"}

    def _tool_call(self, message, tool_call, stream=False, stream_callback=None, tools=None):
        """
        本地工具调用实现
        :param message:
        :param tool_call:
        :param stream:
        :param stream_callback:
        :param tools:
        :return:
        """
        if isinstance(tool_call, dict):
            func_name = tool_call["name"]
            kwargs = json.loads(tool_call["arguments"])
            tool_call_id = tool_call["id"]
        else:
            func_name = tool_call.function.name
            kwargs = json.loads(tool_call.function.arguments)
            tool_call_id = tool_call.id
        # 根据 func_name 调用你自己定义的实际函数
        result = self.function_call.function_call(func_name, kwargs)
        self.chat_history.append(message)
        self.chat_history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps({"weather": result})
        })
        response = self._send_request(self.chat_history, stream=stream, tools=tools)  # 模型继续生成最终回答
        return self._response_handle(response, stream=stream, stream_callback=stream_callback, tools=tools)

    def _stream_output(self, response, stream_callback=None, tools=None):
        """
        流式输出
        :param response:
        :param stream_callback:
        :param tools:
        :return:
        """
        final_resp = ""
        tool_call_info = None
        for chunk in response:
            delta = chunk.choices[0].delta
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tool_call = delta.tool_calls[0]
                if tool_call_info is None:
                    tool_call_info = {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "arguments": ""
                    }
                if tool_call.function and tool_call.function.arguments:
                    tool_call_info["arguments"] += tool_call.function.arguments
                    continue
                # 解析 tool_call
                # return self._tool_call(message, tool_call, tools=tools)
            # 某些 chunk 会是控制信号，没有 content
            text = getattr(delta, "content", None)
            if not text:
                continue
            # 将片段累积到最终结果
            final_resp += text
            # 如果提供了回调，则传递片段
            if stream_callback:
                stream_callback(text)
            # 或者在此处打印
            # print(text, end="", flush=True)
        if tool_call_info:
            # 将工具执行结果作为消息追加给 messages
            messages = {
                "role": "assistant",
                "tool_calls": [{
                    "id": tool_call_info["id"],
                    "type": "function",
                    "function": {
                        "name": tool_call_info["name"],
                        "arguments": tool_call_info["arguments"]
                    }
                }]
            }

            # 工具执行完毕 → 继续第二次调用（继续流式输出最终回答）
            return self._tool_call(messages, tool_call_info, stream=True, stream_callback=stream_callback, tools=tools)
        return final_resp

    def _not_stream_output(self, response, tools=None):
        """
        非流式输出方式
        :param response:
        :param tools:
        :return:
        """
        if tools is not None:
            message = response.choices[0].message
            if hasattr(message, "tool_calls") and message.tool_calls:
                tool_call = message.tool_calls[0]
                # 解析 tool_call
                return self._tool_call(message, tool_call, tools=tools)
        # 普通非流式模式
        return response.choices[0].message.content

    # @staticmethod
    def _response_handle(self, response, stream: bool = False, stream_callback=None, tools=None):
        """
        结果反馈处理
        :param response:
        :param stream:
        :param stream_callback:
        :return:
        """
        if stream:
            return self._stream_output(response, stream_callback, tools)
        return self._not_stream_output(response, tools)
