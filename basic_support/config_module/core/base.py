from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseConfigManager(ABC):
    """配置管理抽象基类，定义配置加载、读取、更新接口"""

    @abstractmethod
    def load_config(self, config_path: str | None = None) -> None:
        """
        加载配置文件
        :param config_path: 配置文件路径（默认读取模块config目录下的config.yaml）
        :raises FileNotFoundError: 配置文件不存在时抛出异常
        :raises ValueError: 配置格式错误或校验失败时抛出异常
        """
        raise NotImplementedError

    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        """
        读取配置值
        :param key: 配置键（支持多级键，如"vector_db.host"）；支持前缀模糊匹配（如"vector_db."获取所有子配置）
        :param default: 默认值（当配置键不存在时返回）
        :return: 配置值（单个值或批量配置字典）
        """
        raise NotImplementedError

    @abstractmethod
    def update_config(self, key: str, value: Any, persist: bool = False) -> bool:
        """
        更新配置值
        :param key: 配置键（支持多级键）
        :param value: 新的配置值
        :param persist: 是否持久化到配置文件（默认False，仅内存更新）
        :return: 更新成功返回True，失败返回False
        """
        raise NotImplementedError

    @abstractmethod
    def validate_config(self, validate_rules: Dict | None = None) -> bool:
        """
        配置校验（支持自定义校验规则）
        :param validate_rules: 自定义校验规则，格式：{配置键: {type: 类型, required: 是否必填, range: 范围}}
        :return: 校验通过返回True，失败抛出异常
        """
        raise NotImplementedError

    @abstractmethod
    def backup_config(self, backup_path: str | None = None) -> str:
        """
        备份配置文件
        :param backup_path: 备份文件路径（默认备份至config/backup目录）
        :return: 备份文件完整路径
        """
        raise NotImplementedError

    @abstractmethod
    def restore_config(self, backup_path: str) -> bool:
        """
        恢复配置文件
        :param backup_path: 备份文件路径
        :return: 恢复成功返回True，失败返回False
        """
        raise NotImplementedError

    @abstractmethod
    def encrypt_sensitive_config(self, keys: List[str], secret_key: str) -> None:
        """
        加密敏感配置
        :param keys: 需要加密的配置键列表（如["vector_db.api_key", "llm.api_key"]）
        :param secret_key: 加密密钥（需与系统安全密钥统一）
        """
        raise NotImplementedError

    @abstractmethod
    def load_remote_config(self, remote_url: str, config_type: str = "yaml") -> None:
        """
        加载远程配置中心配置
        :param remote_url: 远程配置中心地址
        :param config_type: 配置格式（默认yaml）
        """
        raise NotImplementedError
