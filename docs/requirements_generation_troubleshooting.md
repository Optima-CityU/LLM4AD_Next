# Requirements.txt Generation Troubleshooting

## 可能导致没有 requirements.txt 的原因

根据代码分析，以下情况会导致生成的任务包中没有 requirements.txt 文件：

### 1. **LLM 生成失败（最常见）**
**位置**: `src/llm4ad/consultant/build_orchestrator.py:827-832`

```python
try:
    result = await self._provider.generate(prompt, temperature=0.2, max_tokens=1024)
    cleaned = self._sanitize_requirements(result.text)
except Exception as e:
    logger.warning("requirements.txt generation failed: {}", e)
    cleaned = ""
```

**原因**:
- LLM API 调用失败（网络错误、超时、API密钥无效等）
- LLM 返回空响应
- Token 限制（max_tokens=1024 可能不够）

**解决方案**:
- 检查日志中是否有 "requirements.txt generation failed" 警告
- 确保 LLM provider 配置正确
- 考虑增加 max_tokens 限制

### 2. **LLM 返回 "# none" 标记**
**位置**: `src/llm4ad/consultant/build_orchestrator.py:873-876`

```python
joined = "\n".join(lines).strip().lower()
if joined == "# none":
    return ""
```

**原因**:
- LLM 判断项目只需要 Python 标准库
- 这是提示词中的合法响应："If the project genuinely needs no third-party packages (only stdlib + llm4ad), output the single line: `# none`"

**解决方案**:
- 检查任务描述是否足够详细
- 检查生成的代码是否真的只用了标准库

### 3. **所有包都被过滤掉**
**位置**: `src/llm4ad/consultant/build_orchestrator.py:878-911`

**原因**:
- LLM 生成的包名不符合 pip 规范（正则表达式匹配失败）
- 所有包都在标准库黑名单中
- 所有包都是 `llm4ad` 自身

**标准库黑名单**:
```python
stdlib_blocklist = {
    "os", "sys", "json", "subprocess", "pathlib", "typing", "dataclasses",
    "asyncio", "re", "math", "random", "itertools", "functools",
    "collections", "hashlib", "logging", "tempfile", "shutil",
    "importlib", "concurrent", "multiprocessing", "threading", "time",
    "datetime", "io", "copy", "ast", "inspect", "argparse", "enum",
    "abc", "uuid", "csv", "pickle", "warnings", "traceback", "sqlite3",
}
```

**解决方案**:
- 检查 LLM 输出是否包含有效的第三方包名
- 确认提示词改进已生效（新的问题类型和关键词检测）

### 4. **文件写入被跳过**
**位置**: `src/llm4ad/builder/writer.py:44-46`

```python
# Write requirements.txt (LLM-suggested third-party deps)
if blueprint.requirements_txt.strip():
    _write_file(task_dir / "requirements.txt", blueprint.requirements_txt)
```

**原因**:
- `blueprint.requirements_txt` 为空字符串
- 上述任何一个原因导致没有生成有效内容

## 诊断步骤

1. **检查日志输出**
   ```bash
   # 查找 Stage 4 日志
   grep "Stage 4" <log_file>
   ```
   
   正常输出应该是：
   ```
   Stage 4 (requirements): generated N packages (problem_type=X, complexity_tier=Y)
   ```
   
   或者：
   ```
   Stage 4 (requirements): produced no packages (stdlib-only or LLM failure)
   ```

2. **检查是否有警告**
   ```bash
   grep "requirements.txt generation failed" <log_file>
   ```

3. **验证提示词改进是否生效**
   ```python
   from llm4ad.builder.prompts import REQUIREMENTS_PROMPT
   print("computer_vision" in REQUIREMENTS_PROMPT)  # 应该是 True
   ```

4. **手动测试生成**
   创建一个测试脚本直接调用 LLM：
   ```python
   from llm4ad.builder.prompts import REQUIREMENTS_PROMPT
   from llm4ad.infra.provider import OpenAIProvider
   
   provider = OpenAIProvider(...)
   prompt = REQUIREMENTS_PROMPT.format(
       task_description="Train an agent to play Atari games using visual observations",
       problem_type="rl",
       complexity_tier="complex",
       evaluator_code="...",
       algorithm_code="...",
       test_evaluator_code="..."
   )
   result = await provider.generate(prompt, temperature=0.2, max_tokens=1024)
   print(result.text)
   ```

## 临时解决方案

如果生成失败，可以手动创建 requirements.txt：

```bash
cd <task_directory>
cat > requirements.txt << EOF
gymnasium
numpy
torch
opencv-python
Pillow
matplotlib
EOF
```

## 长期改进建议

1. **增加错误重试机制**
   - 在 LLM 调用失败时自动重试 2-3 次
   - 使用不同的 temperature 参数

2. **增加回退策略**
   - 如果 LLM 生成失败，根据 problem_type 自动填充基础包
   - 例如：rl → [gymnasium, numpy, torch, scipy]

3. **增加 max_tokens 限制**
   - 1024 可能不够，建议增加到 2048

4. **添加更详细的日志**
   - 记录 LLM 的原始输出
   - 记录被过滤掉的包及原因

5. **添加验证步骤**
   - 生成后尝试 `pip install --dry-run` 验证
   - 如果验证失败，重新生成
