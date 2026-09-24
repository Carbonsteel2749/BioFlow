"""测试输入数据摘要 Skill。"""

import unittest
import sys
import os

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from runner import run_skill
from skills.common import *


class TestSummarizeInput(unittest.TestCase):
    """测试 SummarizeInput Skill 的各种场景。"""

    def test_summarize_string(self):
        """测试摘要一个字符串。"""
        payload = {"data": "hello world"}
        result = run_skill("summarize_input", payload=payload)

        self.assertTrue(result.succeeded, msg=f"执行失败: {result.error}")
        self.assertEqual(result.data["data_type"], "str")
        self.assertEqual(result.data["summary"], "String with 11 characters.")

    def test_summarize_list(self):
        """测试摘要一个列表。"""
        payload = {"data": [1, 2, 3]}
        result = run_skill("summarize_input", payload=payload)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["data_type"], "list")
        self.assertEqual(result.data["summary"], "List/Tuple with 3 items.")

    def test_summarize_dict(self):
        """测试摘要一个字典。"""
        payload = {"data": {"a": 1, "b": 2}}
        result = run_skill("summarize_input", payload=payload)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["data_type"], "dict")
        self.assertEqual(result.data["summary"], "Dictionary with 2 keys.")

    def test_summarize_none(self):
        """测试摘要 None。"""
        payload = {"data": None}
        result = run_skill("summarize_input", payload=payload)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["data_type"], "NoneType")
        self.assertEqual(result.data["summary"], "Input data is None.")

    def test_summarize_integer(self):
        """测试摘要一个整数。"""
        payload = {"data": 123}
        result = run_skill("summarize_input", payload=payload)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["data_type"], "int")
        self.assertEqual(result.data["summary"], "Data of type 'int'.")

    def test_input_missing_key(self):
        """测试输入缺少 'data' 键。"""
        payload = {"wrong_key": "value"}
        result = run_skill("summarize_input", payload=payload)

        self.assertFalse(result.succeeded)
        self.assertIn("missing required payload keys: data", result.error)


if __name__ == "__main__":
    unittest.main()
