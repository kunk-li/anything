from smart_table_agent.file_processing.file_manager import FileManager
from smart_table_agent.models_manager.function_manager import MyFunctions
from smart_table_agent.models_manager.model_manager import ModelManager


def stream_recallback(info):
    print(info, end="")


class SmartTableAgent:

    def __init__(self):
        self.model_manager = ModelManager()
        self.function_call = MyFunctions()
        self.file_manager = FileManager()
        self._init_info()

    def _init_info(self):
        self.model_manager.register_model("test_model", "DeepSeek")

    def run(self):
        print("现在可以开始聊天了:")
        stream = True
        while True:
            input_info = input()+"中文回答"
            print(input_info)
            content = self.model_manager.multiple_requests("test_model", input_info, stream=stream,
                                                           stream_callback=stream_recallback,
                                                           tools=self.function_call.tools)
            if not stream:
                print(content)
            print()


def start_main():
    smart_table_agent = SmartTableAgent()
    smart_table_agent.run()
