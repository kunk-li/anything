import threading
from importlib import import_module
from typing import Optional


class ModelManager:
    """
    通用模型管理器
    - 支持本地模型类字符串动态导入
    - 支持注册、注销、列出、切换活跃模型
    - 健壮性增强：重复注册、注销不存在、线程安全
    """

    def __init__(self):
        self.models = {}
        self._lock = threading.Lock()  # 线程安全

    # 注册模型实例
    def register_model(self, unique_name: str, llm_class_str: str, model_name: str = None,
                       api_key: Optional[str] = None):
        """
        注册模型实例
        :param unique_name: 模型实例唯一名称
        :param llm_class_str: 模型类名称字符串，例如 "DeepSeek"
        :param model_name: 模型名称
        :param api_key: 模型 key（可选）
        """
        with self._lock:
            if unique_name not in self.models:
                # 动态导入本地模块 models
                models_module = import_module(".models", package=__package__)
                # 从模块中获取类
                if not hasattr(models_module, llm_class_str):
                    raise ValueError(f"models 模块中不存在类 {llm_class_str}")
                llm_class = getattr(models_module, llm_class_str)
                instance = llm_class(model_name=model_name, api_key=api_key)
                self.models[unique_name] = instance
                return instance

    def unregister_model(self, unique_name: str):
        """
        取消注册模型实例
        :param unique_name:
        :return:
        """
        with self._lock:
            if unique_name not in self.models:
                print(f"警告: 尝试注销不存在的模型 '{unique_name}'")
                return None
            return self.models.pop(unique_name)

    # 列出模型
    def list_models(self):
        """
        遍历调用模型名称
        :return:
        """
        return list(self.models.keys())

    def get_model(self, unique_name: str):
        """
        获取模型实例
        :param unique_name: 模型唯一名称
        :return: 模型实例对象，如果不存在返回 None
        """
        if unique_name not in self.models:
            print(f"警告: 模型 '{unique_name}' 未注册")
            return None
        return self.models[unique_name]

    def multiple_requests(self, unique_name: str, input_info: str, stream=True, stream_callback=None, tools=None):
        """
        多轮对话
        :param unique_name:
        :param input_info:
        :param stream:
        :param stream_callback:
        :param tools:
        :return:
        """
        llm = self.get_model(unique_name)
        if llm is not None:
            content = llm.multiple_requests(user_input_info=input_info, stream=stream, stream_callback=stream_callback,
                                            tools=tools)
            return content
        return None

    def single_request(self, unique_name: str, user_input, stream=False, stream_callback=None, tools=None):
        """
        模型单次请求
        :param unique_name:
        :param user_input:
        :param stream: stream=True的时候，启用流示返回
        :param stream_callback:
        :param tools:
        :return:
        """
        llm = self.get_model(unique_name)
        if llm is not None:
            content = llm.single_request(user_input, stream=stream, stream_callback=stream_callback, tools=tools)
            return content
        return None
