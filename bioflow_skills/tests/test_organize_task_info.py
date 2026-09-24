"""测试任务信息整理 Skill。"""

import unittest
import sys
import os

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from runner import run_skill
from skills.common import *


class TestOrganizeTaskInfo(unittest.TestCase):
    """测试 OrganizeTaskInfo Skill 的各种场景。"""

    def test_organize_full_task_info(self):
        """测试完整的任务信息。"""
        task_info = {
            "task_name": "Implement feature X",
            "priority": "High",
            "due_date": "2023-12-25"
        }
        result = run_skill("organize_task_info", payload={"task_info": task_info})

        self.assertTrue(result.succeeded, msg=f"执行失败: {result.error}")
        expected_string = (
            "- Task name: Implement feature X\n"
            "- Priority: High\n"
            "- Due date: 2023-12-25"
        )
        self.assertEqual(result.data["formatted_string"], expected_string)
        self.assertIn("Task information organized for Implement feature X", result.summary)

    def test_organize_partial_task_info(self):
        """测试部分任务信息。"""
        task_info = {"task_name": "Fix bug Y"}
        result = run_skill("organize_task_info", payload={"task_info": task_info})

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["formatted_string"], "- Task name: Fix bug Y")

    def test_organize_empty_task_info(self):
        """测试空的任务信息字典。"""
        result = run_skill("organize_task_info", payload={"task_info": {}})

        self.assertTrue(result.succeeded)
        self.assertEqual(result.data["formatted_string"], "")

    def test_input_not_a_dict(self):
        """测试输入不是一个字典。"""
        result = run_skill("organize_task_info", payload={"task_info": "not a dict"})

        self.assertFalse(result.succeeded)
        self.assertIn("Input 'task_info' must be a dictionary.", result.error)

    def test_input_missing_key(self):
        """测试输入缺少 'task_info' 键。"""
        result = run_skill("organize_task_info", payload={"wrong_key": {}})

        self.assertFalse(result.succeeded)
        self.assertIn("missing required payload keys: task_info", result.error)


if __name__ == "__main__":
    unittest.main()
