# AI构建功能迁移规划：Beta版本覆盖原始版本

## 一、功能识别与对比

### 1.1 原始版本 (consultant-based)

**CLI入口**: `llm4ad chat` ([cli.py:206](src/llm4ad/frontend/cli.py#L206))

**核心模块**: `llm4ad.consultant`
- **会话管理**: `ConsultantSession` (consultant/session.py)
- **对话顾问**: `LLMAdvisor` (consultant/advisor.py) 
- **构建编排**: `BuildOrchestrator` (consultant/build_orchestrator.py)
- **审查循环**: `ReviewLoop` (consultant/review_loop.py)

**工作流程**:
1. **Phase 1: Needs Gathering** - 通过多轮对话收集需求
2. **Phase 2: Building** - 调用BuildOrchestrator自动生成
3. **Phase 3: Review & Iterate** - 展示结果，接受用户反馈和修改

**特点**:
- 三阶段线性流程，阶段明确
- 使用LLM进行对话式需求收集
- 底层使用`builder`模块生成代码（analyzer, creator, validator）
- 支持会话保存和恢复
- 有上下文窗口管理 (ContextLimits)

**Web端集成**: 
- 路由: `/tasks/{task_id}/chat-tune` (backend/app/api/llm4ad/chat_tune.py)
- 模型: `ChatTuneSession`, `ChatTuneMessage` (backend/app/models/chat_tune.py)
- 服务: `chat_tune_service.py` 
- 生成类型: `ChatTuneGenerationKind.AI_BUILD` 和 `CHAT_TUNE`
- 容器运行: `chat_tune_runner.py` 提供隔离的SSE服务

---

### 1.2 Beta版本 (agent-based)

**CLI入口**: `llm4ad chatv2` ([cli.py:1191](src/llm4ad/frontend/cli.py#L1191))

**核心模块**: `llm4ad.agent`
- **运行器**: `runner.py` - 核心的AgentScope ReAct agent实现
- **沙箱**: `sandbox.py` - 路径隔离和安全检查
- **技能系统**: `skill.py` + `skills/` - agent可用的工具集

**关键类型**:
```python
@dataclass
class AgentBuildConfig:
    provider_config: dict[str, Any]
    base_dir: str
    user_content: str = ""
    gathering_context: dict[str, Any] | None = None
    allow_build: bool = False  # GATHER vs BUILD phase gate
    prior_state: dict[str, Any] | None = None
    proposed: dict[str, Any] | None = None
    max_iters: int = 40
    surface: str = "platform"  # "platform" or "cli"
```

**工作流程**:
- **GATHER阶段** (allow_build=False): agent只能检查+提议构建
- **BUILD阶段** (allow_build=True): agent执行构建和自验证

**特点**:
- 单一agent驱动整个流程（conversational）
- 混合策略：agent用工具调用`BuildOrchestrator`，不手写代码
- 双层验证：BuildOrchestrator内部验证 + agent运行测试自验证
- 工具级路径沙箱（resolve_within_sandbox）
- 支持多提供商（OpenAI/Anthropic通过AgentScope）
- 依赖agentscope库（Python >=3.12）

**Web端集成**:
- 生成类型: `ChatTuneGenerationKind.AI_AGENT`
- 容器端点: `POST /agent` (chat_tune_runner.py:68-78, AgentBuildRequest)
- 服务调用: `_run_agent_build()` in chat_tune_service.py
- 功能开关: `ENABLE_AI_AGENT_BUILD` (config.py:246, 默认True)

**前端**:
- 请求参数支持`beta: true`字段切换到agent模式
- 通过同一套chat-tune UI交互

---

## 二、架构差异分析

### 2.1 核心差异

| 维度 | 原始版本 (consultant) | Beta版本 (agent) |
|------|---------------------|------------------|
| **控制流** | 三阶段状态机 | 单agent自主循环 |
| **代码生成** | LLM直接生成（通过builder） | agent调用builder引擎 |
| **交互模式** | 分阶段问答 | 持续对话 |
| **文件操作** | 受限的file_reader工具 | 沙箱化read/list/run_python |
| **验证方式** | 一次性validator | 双层（engine + agent自测） |
| **状态管理** | ConversationState | AgentState (serializable) |
| **依赖** | 无特殊依赖 | 需要agentscope |

### 2.2 共享基础设施

两者都依赖：
- **builder模块**: analyzer, creator, validator, pipeline
- **BuildOrchestrator**: 实际的任务包生成引擎
- **BaseProvider**: LLM提供商抽象
- **全局设置**: ~/.llm4ad/settings.yaml

---

## 三、迁移策略

### 3.1 迁移目标

**完全覆盖原始版本**，意味着：
1. CLI: `llm4ad chat` → 使用agent实现
2. Web: chat-tune默认使用AI_AGENT模式
3. 移除或废弃consultant模块（可选保留作为fallback）
4. 统一用户体验

### 3.2 技术可行性

**优势**:
- ✅ agent版本已经复用`BuildOrchestrator`，核心逻辑一致
- ✅ Web端已有AI_AGENT支持，只需切换默认值
- ✅ 两者共用provider配置，无兼容性问题
- ✅ agent的沙箱机制更安全

**挑战**:
- ⚠️ agentscope依赖（Python >=3.12，新增依赖）
- ⚠️ 用户习惯变化（三阶段→自由对话）
- ⚠️ 调试复杂度增加（agent黑盒vs明确阶段）
- ⚠️ 性能差异（agent需要更多轮次）

---

## 四、详细迁移计划

### 4.1 CLI端迁移

#### 步骤1: 重构`llm4ad chat`命令
**文件**: `src/llm4ad/frontend/cli.py`

**当前代码位置**: 行206-445 (chat_consultant函数)

**改动**:
```python
# 将chat命令的实现从ConsultantSession改为agent runner
@app.command("chat")
def chat_command(
    # 保持参数接口不变（向后兼容）
    provider_name: str | None = ...,
    output: str = ...,
    prompt: str | None = ...,
    # ... 其他参数
):
    """Interactive assistant (AI agent-based)."""
    
    # 内部调用agent runner，而非ConsultantSession
    # 复用chatv2的实现逻辑
    from llm4ad.agent.runner import AgentBuildConfig, run_agent_build
    
    # 映射参数到AgentBuildConfig
    # ... 实现细节
```

**注意事项**:
- 保持CLI参数接口稳定（避免破坏现有脚本）
- `--prompt`、`--non-interactive`等参数需映射到agent的gathering_context
- 会话恢复（--resume）需从ConversationState迁移到agent的prior_state格式

#### 步骤2: 处理会话兼容性
**文件**: 新建或扩展 `src/llm4ad/agent/migration.py`

**功能**: 
```python
def migrate_consultant_session(session_id: str) -> dict[str, Any]:
    """将consultant会话状态转换为agent状态"""
    old_state = ConversationState.load(session_id)
    return {
        "gathering_context": {
            "description": old_state.needs_profile.get("description"),
            "language": old_state.language,
            # ... 其他字段映射
        },
        "proposed": None,  # 如果在build阶段，提取blueprint
    }
```

#### 步骤3: 废弃`chatv2`命令
- 选项A: 移除`chatv2`命令，统一到`chat`
- 选项B: 保留作为别名（`chatv2` = `chat`），后续版本移除

### 4.2 Web端迁移

#### 步骤1: 修改默认生成类型
**文件**: `src/backend/app/services/chat_tune_service.py`

**当前逻辑** (约415-430行):
```python
# 当前：beta参数控制，默认AI_BUILD
if request.beta and settings.ENABLE_AI_AGENT_BUILD:
    generation_kind = ChatTuneGenerationKind.AI_AGENT
else:
    generation_kind = ChatTuneGenerationKind.AI_BUILD
```

**修改为**:
```python
# 新逻辑：默认使用AI_AGENT
if settings.ENABLE_AI_AGENT_BUILD:
    generation_kind = ChatTuneGenerationKind.AI_AGENT
else:
    # fallback到旧版本
    generation_kind = ChatTuneGenerationKind.AI_BUILD
```

**影响**:
- 所有新任务默认使用agent模式
- 旧任务根据历史generation_kind继续工作
- 可通过环境变量`ENABLE_AI_AGENT_BUILD=false`回滚

#### 步骤2: 前端UI调整
**文件**: `src/frontend/src/components/Evolution/TaskDetail/ChatTuneView.tsx`

**可能改动**:
- 移除或隐藏"beta"开关（如果存在UI控制）
- 更新提示文案（从"三阶段"改为"智能助手"）
- 调整loading状态展示（agent模式可能更长）

#### 步骤3: 数据库兼容性
**文件**: 新建迁移 `src/backend/app/alembic/versions/xxx_deprecate_ai_build.py`

**目标**:
- 保留现有generation_kind枚举值（向后兼容）
- 可选：添加数据迁移将旧AI_BUILD会话标记为deprecated
- 不要删除现有数据

### 4.3 模块重构

#### 步骤1: consultant模块处理
**选项A - 完全移除**:
```bash
# 删除consultant目录
rm -rf src/llm4ad/consultant/
# 更新导入引用
find src -name "*.py" -exec sed -i 's/from llm4ad.consultant/# DEPRECATED/g' {} \;
```

**选项B - 标记废弃（推荐）**:
```python
# src/llm4ad/consultant/__init__.py
import warnings

warnings.warn(
    "llm4ad.consultant is deprecated and will be removed in v2.0. "
    "Use llm4ad.agent instead.",
    DeprecationWarning,
    stacklevel=2
)

# 保留现有导出以维持兼容性
from .session import ConsultantSession
__all__ = ["ConsultantSession"]
```

**推荐**: 选项B，保留1-2个版本周期，给用户迁移时间

#### 步骤2: 更新文档
**文件**: `CLAUDE.md`, `README.md`, `docs/`

- 更新"Common Development Commands"中的chat说明
- 添加迁移指南（旧->新）
- 更新架构图
- 标注consultant为deprecated

#### 步骤3: 依赖管理
**文件**: `pyproject.toml`

**当前**: agentscope是基础依赖
**确认**: 
```toml
[project]
dependencies = [
    "agentscope>=0.1.0",  # 确认最低版本要求
    # ...
]
requires-python = ">=3.12"  # agent要求
```

**兼容性考虑**:
- 如果保留consultant作为fallback，可以让agentscope成为可选依赖
- 但推荐全面迁移，统一依赖

---

## 五、回滚与风险控制

### 5.1 功能开关
**保留** `ENABLE_AI_AGENT_BUILD` 环境变量作为紧急回滚开关：

```python
# config.py
ENABLE_AI_AGENT_BUILD: bool = True

# 如果发现agent有严重bug，可以快速回滚：
# export ENABLE_AI_AGENT_BUILD=false
```

### 5.2 灰度发布（Web端）
**阶段1**: 默认AI_AGENT，但保留AI_BUILD代码路径
**阶段2**: 监控1-2周，收集用户反馈
**阶段3**: 确认稳定后，移除consultant相关代码

### 5.3 CLI回滚方案
**保留chatv2**作为别名，如果chat出问题：
```bash
# 临时回滚：恢复旧版本
git revert <migration-commit>

# 或用户手动回退到旧版CLI
pip install llm4ad==<old-version>
```

---

## 六、测试计划

### 6.1 单元测试
**新增测试文件**: `tests/agent/test_migration.py`

```python
def test_consultant_state_migration():
    """测试会话状态迁移"""
    
def test_agent_builder_integration():
    """测试agent调用BuildOrchestrator"""
    
def test_sandbox_security():
    """测试沙箱路径隔离"""
```

### 6.2 集成测试
**场景覆盖**:
1. CLI端到端流程：`llm4ad chat --prompt "..." --non-interactive`
2. Web端对话流程：创建任务 → chat-tune → 构建成功
3. 会话恢复：中断后--resume继续
4. 多提供商：OpenAI, Anthropic, 自定义base_url

### 6.3 回归测试
**确保不破坏**:
- `llm4ad run` 流程（核心orchestrator不变）
- builder模块（analyzer/creator/validator）
- 现有任务的chat-tune历史查看

---

## 七、实施时间线

### 阶段一：准备（1-2天）
- [ ] 代码审计：确认consultant的所有使用位置
- [ ] 依赖检查：验证agentscope在CI中正常工作
- [ ] 编写迁移脚本（会话状态转换）

### 阶段二：CLI迁移（2-3天）
- [ ] 重构`llm4ad chat`命令
- [ ] 添加会话兼容性处理
- [ ] 更新CLI测试
- [ ] 更新CLI文档

### 阶段三：Web端迁移（2-3天）
- [ ] 修改默认generation_kind
- [ ] 前端UI调整（如需）
- [ ] 添加Web端测试
- [ ] 数据库迁移脚本

### 阶段四：清理与文档（1-2天）
- [ ] 标记consultant为deprecated
- [ ] 更新所有文档
- [ ] 添加迁移指南
- [ ] 发布changelog

### 阶段五：监控与稳定（1-2周）
- [ ] 灰度发布，监控错误率
- [ ] 收集用户反馈
- [ ] 修复发现的问题
- [ ] 确认稳定后移除旧代码

**总计**: 约2-3周完成完整迁移

---

## 八、关键决策点

### 决策1: 是否立即移除consultant？
**推荐**: 否，保留1-2个版本作为deprecated fallback

**理由**:
- 降低迁移风险
- 给用户适应时间
- 便于问题排查

### 决策2: CLI参数接口是否保持兼容？
**推荐**: 是，保持参数名称和默认值不变

**理由**:
- 避免破坏现有脚本和CI流程
- 内部实现切换对用户透明

### 决策3: 是否强制要求agentscope？
**推荐**: 是，作为基础依赖

**理由**:
- 简化维护（不用维护两套实现）
- Python 3.12已经普及
- agentscope是轻量级依赖

### 决策4: Web端是否提供切换开关？
**推荐**: 仅保留环境变量级开关，不提供UI开关

**理由**:
- 简化UI，避免用户困惑
- 管理员级紧急回滚能力已足够
- 鼓励全面迁移，而非长期并存

---

## 九、潜在问题与解决方案

### 问题1: agent性能比consultant慢
**表现**: agent需要更多ReAct轮次
**解决**: 
- 优化agent prompt，减少无效工具调用
- 调整max_iters参数
- 前端增加进度提示

### 问题2: 用户不适应自由对话模式
**表现**: 用户反馈"不知道该说什么"
**解决**:
- 增强agent的引导能力（主动提问）
- 提供预设模板/例子
- 文档中添加使用示例

### 问题3: 调试困难（agent黑盒）
**表现**: 出错时难以定位问题
**解决**:
- 增强日志记录（记录每次tool call）
- 添加调试模式（`--debug`输出agent思考过程）
- 提供replay工具（重放agent状态）

### 问题4: 旧会话无法恢复
**表现**: --resume失败或行为异常
**解决**:
- 实现状态转换函数（consultant → agent）
- 明确告知用户旧会话可能不完全兼容
- 提供手动导出/导入机制

---

## 十、成功标准

### 功能层面
- ✅ CLI `llm4ad chat` 使用agent实现，所有参数正常工作
- ✅ Web端chat-tune默认使用AI_AGENT，交互流畅
- ✅ 生成的任务包质量与consultant版本一致或更好
- ✅ 所有现有测试通过，新增agent测试覆盖>80%

### 性能层面
- ✅ 端到端时间增加不超过30%
- ✅ 成功率 ≥95%（与consultant基线对比）
- ✅ 用户满意度调查 ≥4/5星

### 维护层面
- ✅ 代码重复度降低（移除consultant后）
- ✅ bug修复只需在agent分支进行
- ✅ 文档清晰，新用户能快速上手

---

## 十一、附录

### A. 相关文件清单

**CLI相关**:
- `src/llm4ad/frontend/cli.py` - 命令行入口

**原始版本**:
- `src/llm4ad/consultant/session.py` - 会话编排
- `src/llm4ad/consultant/advisor.py` - LLM顾问
- `src/llm4ad/consultant/build_orchestrator.py` - 构建编排
- `src/llm4ad/consultant/state.py` - 状态管理

**Beta版本**:
- `src/llm4ad/agent/runner.py` - agent核心逻辑
- `src/llm4ad/agent/sandbox.py` - 沙箱工具
- `src/llm4ad/agent/skill.py` - 技能定义

**Web后端**:
- `src/backend/app/api/llm4ad/chat_tune.py` - API路由
- `src/backend/app/services/chat_tune_service.py` - 业务逻辑
- `src/backend/app/tasks/chat_tune_runner.py` - 容器运行器
- `src/backend/app/models/chat_tune.py` - 数据模型

**Web前端**:
- `src/frontend/src/components/Evolution/TaskDetail/ChatTuneView.tsx` - UI组件

**共享基础**:
- `src/llm4ad/builder/` - 代码生成引擎（两者共用）

### B. 关键代码位置

| 功能 | 文件 | 行号 |
|------|------|------|
| CLI chat命令 | cli.py | 206-445 |
| CLI chatv2命令 | cli.py | 1191-1509 |
| Web生成类型判断 | chat_tune_service.py | ~415-430 |
| Agent runner入口 | runner.py | 1-200 |
| 功能开关配置 | config.py | 246 |
| 容器agent端点 | chat_tune_runner.py | 68-78 |

### C. 参考资源
- AgentScope文档: https://github.com/modelscope/agentscope
- 项目CLAUDE.md: 本仓库根目录
- 原始设计文档: (如有，请补充链接)

---

**文档版本**: 1.0  
**创建日期**: 2026-07-14  
**最后更新**: 2026-07-14  
**维护者**: QingL2000
