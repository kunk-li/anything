import os

from openai import OpenAI


class Kimi(object):
    api_key = os.environ.get("KIMI_API_KEY")
    base_url = "https://api.moonshot.cn/v1"
    model_name = "moonshot-v1-8k"

    def __init__(self):
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def request(self, user_input, temperature=0.3):
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "user", "content": user_input}
            ],
            temperature=temperature,
        )
        resp = response.choices[0].message.content
        return resp
