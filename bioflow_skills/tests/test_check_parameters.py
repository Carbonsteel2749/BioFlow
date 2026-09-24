"""测试参数检查 Skill。"""

import unittest
import sys
import os

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from runner import run_skill
from skills.common import *


class TestCheckParameters(unittest.TestCase):
    """测试 CheckParameters Skill 的各种场景。"""

    def test_valid_parameters(self):
        """测试所有参数都有效。"""
        parameters = {"age": 25, "name": "John"}
        rules = {"age": {"type": "int"}, "name": {"type": "str", "required": True}}
        result = run_skill("check_parameters", payload={"parameters": parameters, "rules": rules})

        self.assertTrue(result.succeeded, msg=f"执行失败: {result.error}")
        self.assertTrue(result.data["is_valid"])
        self.assertEqual(len(result.data["errors"]), 0)

    def test_wrong_type(self):
        """测试参数类型错误。"""
        parameters = {"age": "25"}
        rules = {"age": {"type": "int"}}
        result = run_skill("check_parameters", payload={"parameters": parameters, "rules": rules})

        self.assertTrue(result.succeeded)
        self.assertFalse(result.data["is_valid"])
        self.assertIn("Parameter 'age' has wrong type: expected int, got str", result.data["errors"][0])

    def test_required_missing(self):
        """测试缺少必需的参数。"""
        parameters = {"name": None}
        rules = {"name": {"type": "str", "required": True}}
        result = run_skill("check_parameters", payload={"parameters": parameters, "rules": rules})

        self.assertTrue(result.succeeded)
        self.assertFalse(result.data["is_valid"])
        self.assertIn("Parameter 'name' is required.", result.data["errors"][0])

    def test_invalid_input(self):
        """测试输入不是字典。"""
        parameters = "not a dict"
        rules = {}
        result = run_skill("check_parameters", payload={"parameters": parameters, "rules": rules})

        self.assertFalse(result.succeeded)
        self.assertIn("'parameters' and 'rules' must be dictionaries.", result.error)

    def test_missing_keys(self):
        """测试缺少 'parameters' 或 'rules' 键。"""
        result = run_skill("check_parameters", payload={"parameters": {}})

        self.assertFalse(result.succeeded)
        self.assertIn("missing required payload keys: rules", result.error)


if __name__ == "__main__":
    unittest.main()
