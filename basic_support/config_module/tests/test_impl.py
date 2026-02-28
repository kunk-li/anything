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


if __name__ == "__main__":
    unittest.main()
