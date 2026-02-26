import os
import time
import jwt
import requests


class KeLing:

    # pip install PyJWT
    def __init__(self):
        self._base_url = "https://api.klingai.com"
        self._image_generation_url = self._base_url + "/v1/images/generations"
        self._access_key_id = os.environ.get("access_key_id")
        self._access_key_secret = os.environ.get("access_key_secret")
        self.model_name = "kling-v1-5"
        # self.model_name = "kling-v2"

    def encode_jwt_token(self):
        headers = {
            "alg": "HS256",
            "typ": "JWT"
        }
        payload = {
            "iss": self._access_key_id,
            "exp": int(time.time()) + 1800,  # 有效时间，此处示例代表当前时间+1800s(30min)
            "nbf": int(time.time()) - 5  # 开始生效的时间，此处示例代表当前时间-5秒
        }
        token = jwt.encode(payload, self._access_key_secret, headers=headers)
        return token

    def image_generation(self, image_prompt, image_number=1, ratio="3:4"):
        api_token = self.encode_jwt_token()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}"
        }
        data = {
            "model_name": self.model_name,
            "aspect_ratio": ratio,
            "n": image_number,
            "prompt": image_prompt
        }
        response = requests.post(self._image_generation_url, headers=headers, json=data)
        response_json = response.json()
        task_id = response_json.get("data", {}).get("task_id")
        images_url = []
        while True:
            response = requests.get(f"{self._image_generation_url}/{task_id}", headers=headers)
            data = response.json().get("data", {})
            task_status = data.get("task_status", "failed")

            # submitted（已提交）、processing（处理中）、succeed（成功）、failed（失败）
            if task_status == "succeed":
                images = data.get("task_result", {}).get("images", [])
                # logger.info(f"{image_prompt}---生成图片内容： {images}")
                for image_info in images:
                    image_url = image_info.get("url")
                    images_url.append(image_url)
                break
            elif task_status == "failed":

                return [], False
            else:
                time.sleep(2)
        return images_url, True

    @staticmethod
    def save_image_from_url(url, save_path=".", filename=None):
        """
        从URL下载图片并保存到本地
        参数:
            url: 图片的URL地址
            save_path: 保存的目录路径
            filename: 保存的文件名(可选)，如果不指定则从URL或当前时间生成
        """
        try:
            # 创建保存目录(如果不存在)
            os.makedirs(save_path, exist_ok=True)

            # 发送HTTP GET请求获取图片
            response = requests.get(url, stream=True)
            response.raise_for_status()  # 检查请求是否成功

            # 确定文件名
            if not filename:
                # 尝试从URL中提取文件名
                filename = os.path.basename(url.split("/")[-1])  # 去除URL参数
                if not filename or '.' not in filename:
                    # 如果无法从URL获取有效文件名，使用时间戳
                    from datetime import datetime
                    filename = f"image_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"

            # 完整的文件路径
            filepath = os.path.join(save_path, filename)

            # 写入文件
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)

            # logger.info(f"图片已保存到: {filepath}")
            return filepath

        except Exception as e:
            print(f"保存图片失败: {e}")
            return None


if __name__ == '__main__':
    prompt = """可爱的白色小猫咪，仰望天空，简笔画，手绘，背景留白，治愈系插画，富有童趣，笔触简单，有线条感，人物头顶空白处写卡通笔体文字“人生最大的喜悦是每个人都说你做不到，你却完成了它。”"""
    k = KeLing()
    images_url, status = k.image_generation(prompt)
    if status:
        for image in images_url:
            k.save_image_from_url(image)
