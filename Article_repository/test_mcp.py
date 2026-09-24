import os
import requests
import json
import sys

# ---------- 配置 ----------
BASE_URL = os.getenv("AI_LOCALBASE_URL", "http://localhost:8080")
MCP_KEY = os.getenv("AI_LOCALBASE_MCP_KEY")      # 可选
KB_ID = os.getenv("AI_LOCALBASE_KB_ID")

# 调试输出：检查环境变量
print("=== 环境变量检查 ===")
print(f"AI_LOCALBASE_URL: {BASE_URL}")
print(f"AI_LOCALBASE_KB_ID: {KB_ID}")
print(f"AI_LOCALBASE_MCP_KEY: {'已设置' if MCP_KEY else '未设置'}")
if MCP_KEY:
    print(f"MCP_KEY 长度: {len(MCP_KEY)}")
    print(f"MCP_KEY 前10字符: {MCP_KEY[:10]}")
    # 检查是否全是 ASCII 字符
    try:
        MCP_KEY.encode('ascii')
        print("MCP_KEY: 纯 ASCII 字符 ✓")
    except UnicodeEncodeError:
        print("MCP_KEY: 包含非 ASCII 字符 ✗（这可能导致 HTTP 头错误）")
        # 尝试用 latin-1 编码
        try:
            MCP_KEY.encode('latin-1')
            print("MCP_KEY: 可以 latin-1 编码 ✓")
        except UnicodeEncodeError as e:
            print(f"MCP_KEY: 不能 latin-1 编码 ✗ - {e}")
print("=" * 30)

# MCP 端点
MCP_URL = f"{BASE_URL}/mcp"
print(f"MCP URL: {MCP_URL}")

# 请求头 - 确保只包含 ASCII 字符
HEADERS = {"Content-Type": "application/json"}
if MCP_KEY:
    # 清理 key，移除可能的不可见字符
    MCP_KEY = MCP_KEY.strip()
    # 验证是否可以安全用于 HTTP 头
    try:
        MCP_KEY.encode('latin-1')
        HEADERS["Authorization"] = f"Bearer {MCP_KEY}"
    except UnicodeEncodeError:
        print("❌ MCP_KEY 包含无法用于 HTTP 头的字符，请检查环境变量设置")
        print("   提示：运行 'echo $AI_LOCALBASE_MCP_KEY | xxd' 查看具体内容")
        sys.exit(1)

print(f"请求头: {HEADERS}")

# ---------- 工具函数 ----------
def mcp_request(method, params=None, request_id=1):
    """发送 JSON-RPC 请求"""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {}
    }
    
    print(f"\n--- 发送请求: {method} ---")
    print(f"URL: {MCP_URL}")
    print(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
    
    try:
        resp = requests.post(MCP_URL, json=payload, headers=HEADERS, timeout=10)
        print(f"状态码: {resp.status_code}")
        print(f"响应头: {dict(resp.headers)}")
        
        resp.raise_for_status()
        data = resp.json()
        print(f"响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
        
        if "error" in data:
            raise Exception(f"MCP Error: {data['error']}")
        return data["result"]
    except requests.exceptions.ConnectionError:
        print(f"❌ 连接失败：无法连接到 {MCP_URL}")
        print("   请检查：1) ai-localbase 是否启动  2) URL 是否正确")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"❌ 请求超时")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        raise

def list_tools():
    """获取可用工具列表"""
    print("\n=== 获取工具列表 ===")
    try:
        result = mcp_request("tools/list")
        tools = result.get("tools", [])
        print(f"\n找到 {len(tools)} 个工具:")
        for t in tools:
            print(f"  • {t['name']}: {t.get('description','无描述')}")
        return {t["name"]: t for t in tools}
    except Exception as e:
        print(f"获取工具列表失败: {e}")
        print("可能 ai-localbase 不支持 tools/list 方法，继续尝试上传...")
        return {}

def upload_document(file_name, content):
    """上传文本文档到知识库"""
    print(f"\n=== 上传文档: {file_name} ===")
    result = mcp_request("tools/call", {
        "name": "upload_text_document",
        "arguments": {
            "knowledgeBaseId": KB_ID,
            "fileName": file_name,
            "content": content
        }
    }, request_id=2)
    print(f"✅ 上传成功")
    return result

def search_knowledge(query, tool_name="search_knowledge"):
    """调用知识库查询工具"""
    print(f"\n=== 查询知识库: '{query}' ===")
    result = mcp_request("tools/call", {
        "name": tool_name,
        "arguments": {
            "knowledgeBaseId": KB_ID,
            "query": query
        }
    }, request_id=3)
    return result

# ---------- 主流程 ----------
if __name__ == "__main__":
    # 0. 检查知识库 ID
    if not KB_ID:
        print("❌ 请先设置环境变量 AI_LOCALBASE_KB_ID")
        print("   export AI_LOCALBASE_KB_ID='your-kb-id'")
        sys.exit(1)

    # 1. 先测试基本连接（可选的健康检查）
    try:
        test_resp = requests.get(BASE_URL, timeout=5)
        print(f"\n✅ 服务可达: {BASE_URL} (状态码: {test_resp.status_code})")
    except:
        print(f"\n⚠️  无法访问 {BASE_URL}，但仍尝试 MCP 端点...")

    # 2. 获取工具列表（可能失败，不影响后续）
    tools = list_tools()

    # 3. 上传测试文档
    try:
        test_file = "python-test-article"
        test_content = "这是一篇由 Python 脚本上传的测试文章，用于验证知识库写入功能。内容包含独特的标识：pytest-verify-2026"
        upload_document(test_file, test_content)
    except Exception as e:
        print(f"❌ 上传失败，但继续尝试查询...")

    # 4. 查询验证
    search_tool = None
    for name in ["search_knowledge", "query_documents", "retrieve_context", "search"]:
        if name in tools:
            search_tool = name
            break

    if search_tool:
        try:
            results = search_knowledge("pytest-verify-2026", tool_name=search_tool)
            result_str = json.dumps(results, ensure_ascii=False)
            if "pytest-verify-2026" in result_str:
                print("\n✅✅✅ 验证成功：文档已成功写入知识库并可被检索到！")
            else:
                print("\n⚠️  查询结果中未找到测试标识，可能需要等待索引或调整参数")
        except Exception as e:
            print(f"\n❌ 查询失败: {e}")
    else:
        print("\n⚠️  未找到查询工具，跳过验证步骤")
        print("请手动检查 ai-localbase 的日志或管理界面确认文档是否入库")