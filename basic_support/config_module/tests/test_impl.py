import os
import tempfile
import unittest
import shutil

from config_module.core.impl import ConfigManager


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.config_manager = ConfigManager()
        self.temp_config_dir = tempfile.mkdtemp()
        self.temp_config_path = os.path.join(self.temp_config_dir, "config.yaml")
        with open(self.temp_config_path, "w", encoding="utf-8") as f:
            f.write(
                """
global:
  env: "test"
  hot_reload: false
llm:
  temperature: 0.7
vector_db:
  vector_dimension: 768
security:
  auth_enabled: true
validate_rules:
  llm.temperature:
    type: "float"
    required: true
    range: [0.0, 1.0]
  vector_db.vector_dimension:
    type: "int"
    required: true
    range: [128, 2048]
  security.auth_enabled:
    type: "bool"
    required: true
                """.strip()
            )

    def tearDown(self):
        shutil.rmtree(self.temp_config_dir)

    def test_load_config(self):
        self.config_manager.load_config(self.temp_config_path)
        self.assertEqual(self.config_manager.get_config("global.env"), "test")

        with self.assertRaises(FileNotFoundError):
            self.config_manager.load_config(os.path.join(self.temp_config_dir, "non_existent.yaml"))

    def test_get_config(self):
        self.config_manager.load_config(self.temp_config_path)

        self.assertEqual(self.config_manager.get_config("global.env"), "test")
        self.assertEqual(self.config_manager.get_config("global.non_existent", "default"), "default")

        llm_cfg = self.config_manager.get_config("llm.")
        self.assertIn("temperature", llm_cfg)

    def test_update_config(self):
        self.config_manager.load_config(self.temp_config_path)

        self.assertTrue(self.config_manager.update_config("llm.temperature", 0.5))
        self.assertEqual(self.config_manager.get_config("llm.temperature"), 0.5)

        self.assertTrue(self.config_manager.update_config("global.env", "production", persist=True))
        self.config_manager.load_config(self.temp_config_path)
        self.assertEqual(self.config_manager.get_config("global.env"), "production")

    def test_validate_config(self):
        self.config_manager.load_config(self.temp_config_path)
        self.assertTrue(self.config_manager.validate_config())

        self.config_manager.update_config("llm.temperature", 1.5)
        with self.assertRaises(ValueError):
            self.config_manager.validate_config()

    def test_validate_config_bool_range_enum(self):
        # bool 是 int 的子类: 带 range 的布尔配置应走枚举校验, 不能误入数值 [lo, hi] 比较
        self.config_manager.load_config(self.temp_config_path)

        # range 为单元素枚举 [True]: True 合法
        self.assertTrue(
            self.config_manager.validate_config(
                {"security.auth_enabled": {"type": "bool", "range": [True]}}
            )
        )

        # range 为 [False]: 当前值 True 不在枚举内, 应抛 ValueError 而非走数值分支
        with self.assertRaises(ValueError):
            self.config_manager.validate_config(
                {"security.auth_enabled": {"type": "bool", "range": [False]}}
            )

    def test_backup_and_restore_config(self):
        self.config_manager.load_config(self.temp_config_path)

        backup_path = self.config_manager.backup_config(backup_path=self.temp_config_dir)
        self.assertTrue(os.path.exists(backup_path))

        self.assertTrue(self.config_manager.update_config("global.env", "modified", persist=True))
        self.assertTrue(self.config_manager.restore_config(backup_path))
        self.assertEqual(self.config_manager.get_config("global.env"), "test")

    def test_encrypt_sensitive_config(self):
        self.config_manager.load_config(self.temp_config_path)

        secret_key = "test_secret_key_123456"
        self.config_manager.encrypt_sensitive_config(keys=["llm.temperature"], secret_key=secret_key)

        # 文件中应为加密值
        with open(self.temp_config_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ENC::", content)

        # 重新加载后自动解密，且类型为 float
        self.config_manager.load_config(self.temp_config_path)
        self.config_manager.secret_key = secret_key
        self.config_manager.load_config(self.temp_config_path)
        self.assertAlmostEqual(self.config_manager.get_config("llm.temperature"), 0.7, places=6)


class TestGetEffectiveValue(unittest.TestCase):
    """显式优先级解析: env_var > yaml > default"""

    def setUp(self):
        self.cfg = ConfigManager()
        self.cfg.load_config()
        # 暂存可能存在的环境变量,确保测试不被外部环境干扰
        self._env_snapshot = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def test_env_var_overrides_yaml(self):
        """L1 环境变量应覆盖 L4 yaml 默认"""
        os.environ["ANYTHING_TEST_ENV_OVERRIDE"] = "from_env"
        v = self.cfg.get_effective_value(
            key="global.env",
            env_var="ANYTHING_TEST_ENV_OVERRIDE",
            default="from_default",
        )
        self.assertEqual(v, "from_env")

    def test_yaml_used_when_no_env(self):
        """没设环境变量时走 yaml"""
        os.environ.pop("ANYTHING_TEST_ENV_OVERRIDE", None)
        v = self.cfg.get_effective_value(
            key="global.env",
            env_var="ANYTHING_TEST_ENV_OVERRIDE",
            default="from_default",
        )
        # config.yaml 中 global.env 是 "development"
        self.assertEqual(v, "development")

    def test_default_used_when_neither_env_nor_yaml(self):
        """env 和 yaml 都没有时返回 default"""
        os.environ.pop("ANYTHING_NO_SUCH_KEY", None)
        v = self.cfg.get_effective_value(
            key="nonexistent.path",
            env_var="ANYTHING_NO_SUCH_KEY",
            default="fallback",
        )
        self.assertEqual(v, "fallback")

    def test_empty_env_treated_as_missing(self):
        """空字符串环境变量应视为未设置(走 yaml/default)"""
        os.environ["ANYTHING_TEST_EMPTY"] = ""
        v = self.cfg.get_effective_value(
            key="nonexistent.path",
            env_var="ANYTHING_TEST_EMPTY",
            default="fallback",
        )
        self.assertEqual(v, "fallback")

    def test_no_env_var_param_falls_through_to_yaml(self):
        """env_var=None 时只走 yaml + default"""
        v = self.cfg.get_effective_value(
            key="global.env",
            env_var=None,
            default="x",
        )
        self.assertEqual(v, "development")

    def test_value_type_int_from_env(self):
        """value_type=int 把环境变量 str 转 int"""
        os.environ["ANYTHING_TEST_INT"] = "42"
        v = self.cfg.get_effective_value(
            key="nonexistent.path",
            env_var="ANYTHING_TEST_INT",
            default=0,
            value_type=int,
        )
        self.assertEqual(v, 42)
        self.assertIsInstance(v, int)

    def test_value_type_bool_truthy(self):
        for s in ("1", "true", "True", "yes", "on"):
            os.environ["ANYTHING_TEST_BOOL"] = s
            v = self.cfg.get_effective_value(
                key="nonexistent.path",
                env_var="ANYTHING_TEST_BOOL",
                default=False,
                value_type=bool,
            )
            self.assertTrue(v, f"expect True for {s!r}")

    def test_value_type_bool_falsy(self):
        for s in ("0", "false", "no", "off", ""):
            if s == "":
                # 空串视为未设, 走 default
                os.environ["ANYTHING_TEST_BOOL"] = s
                v = self.cfg.get_effective_value(
                    "nonexistent.path", env_var="ANYTHING_TEST_BOOL",
                    default=False, value_type=bool,
                )
                self.assertFalse(v)
                continue
            os.environ["ANYTHING_TEST_BOOL"] = s
            v = self.cfg.get_effective_value(
                "nonexistent.path", env_var="ANYTHING_TEST_BOOL",
                default=True, value_type=bool,
            )
            self.assertFalse(v, f"expect False for {s!r}")

    def test_value_type_conversion_failure_falls_back_to_default(self):
        """env_var 设了非法数字时应回退到 default 而非崩"""
        os.environ["ANYTHING_TEST_BAD_INT"] = "not_a_number"
        v = self.cfg.get_effective_value(
            "nonexistent.path",
            env_var="ANYTHING_TEST_BAD_INT",
            default=99,
            value_type=int,
        )
        self.assertEqual(v, 99)


class TestCheckRequiredSecrets(unittest.TestCase):
    """check_required_secrets: 扫描未填 ${XXX} 占位符"""

    def setUp(self):
        self.cfg = ConfigManager()
        self.cfg.load_config()
        self._env_snapshot = dict(os.environ)
        # 清掉已设环境变量,让扫描能稳定地"发现未填"
        for k in ("OPENAI_API_KEY", "DASHSCOPE_API_KEY", "VECTOR_DB_API_KEY",
                  "JWT_SECRET", "SENSITIVE_CONFIG_SECRET", "API_KEY_1"):
            os.environ.pop(k, None)
        # 重新 load 让 substitute_env 看到当前 env 状态
        self.cfg.load_config()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def test_detects_unfilled_secrets(self):
        """yaml 默认配置含 ${OPENAI_API_KEY} 等占位, 当 env 没设时应被检出"""
        unfilled = self.cfg.check_required_secrets()
        # 至少应该包含几个我们已知的 yaml 占位
        self.assertIn("OPENAI_API_KEY", unfilled)
        self.assertIn("DASHSCOPE_API_KEY", unfilled)
        self.assertIn("JWT_SECRET", unfilled)

    def test_returns_sorted_unique_list(self):
        unfilled = self.cfg.check_required_secrets()
        self.assertEqual(unfilled, sorted(unfilled))
        self.assertEqual(len(unfilled), len(set(unfilled)))

    def test_filled_secrets_not_in_result(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.cfg.load_config()  # 重新替换占位
        unfilled = self.cfg.check_required_secrets()
        self.assertNotIn("OPENAI_API_KEY", unfilled)


if __name__ == "__main__":
    unittest.main()
