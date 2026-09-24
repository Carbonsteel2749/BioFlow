"""测试文件类型识别 Skill。"""

import unittest
import sys
import os

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from runner import run_skill
from skills.common import *

class TestFileTypeIdentifier(unittest.TestCase):
    """测试 FileTypeIdentifier Skill 的各种场景。"""

    def test_run_skill_not_found(self):
        """测试调用一个不存在的 skill。"""
        result = run_skill("a_skill_that_does_not_exist", payload={})
        self.assertFalse(result.succeeded)
        self.assertEqual(result.status, "failed")
        self.assertIn("Skill not found", result.error)

    def test_identify_pdf(self):
        """测试正常识别 PDF 文件。"""
        payload = {"file_path": "/path/to/document.pdf"}
        result = run_skill("file_type_identifier", payload=payload)

        self.assertTrue(result.succeeded, msg=f"执行失败: {result.error}")
        self.assertEqual(result.data["file_type"], "pdf")
        self.assertIn("identified as 'pdf'", result.summary)

    def test_identify_no_extension(self):
        """测试没有扩展名的文件路径。"""
        payload = {"file_path": "README"}
        result = run_skill("file_type_identifier", payload=payload)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["file_type"], "unknown")
        self.assertIn("no extension", result.summary)

    def test_input_missing_key(self):
        """测试输入缺少必要的 'file_path' 键。"""
        payload = {"some_other_key": "value"}
        result = run_skill("file_type_identifier", payload=payload)

        self.assertFalse(result.succeeded)
        self.assertIn("missing required payload keys: file_path", result.error)

    def test_input_invalid_type(self):
        """测试 'file_path' 不是字符串类型。"""
        payload = {"file_path": 12345}
        result = run_skill("file_type_identifier", payload=payload)

        self.assertFalse(result.succeeded)
        self.assertIn("file_path must be a non-empty string", result.error)

    def test_input_empty_string(self):
        """测试 'file_path' 为空字符串。"""
        payload = {"file_path": "   "}
        result = run_skill("file_type_identifier", payload=payload)

        self.assertFalse(result.succeeded)
        self.assertIn("file_path must be a non-empty string", result.error)


if __name__ == "__main__":
    unittest.main()
