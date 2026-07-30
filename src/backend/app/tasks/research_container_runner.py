"""容器化 AutoResearch pipeline 执行入口。

在 Docker 容器内执行 researchclaw ``execute_pipeline``，与宿主完全隔离——只挂载
本 turn 的 ``run_dir``，看不到其他用户目录。无需命令行参数：宿主把 run_dir 挂到
``DATA_DIR``，并在启动前写入加密配置（``CONFIG_FILENAME``，敏感字段对称加密），
容器解密后运行。

通信全走 NDJSON 事件文件（``EVENTS_FILENAME``），宿主侧 :class:`ContainerJob`
tail 后转发到 Redis/DB：
- ARC 日志、stage 进度（``__progress__``）；
- **终态**（``__result__`` 事件，marker=gate/done/failed）——不再写独立的终态标记
  文件（那个文件按 session 复用同一路径，容器被 kill/崩时宿主会读到上一轮残留）。
  容器若在发 ``__result__`` 前崩溃/被 kill，宿主据容器退出码（``result.status``）
  兜底判定 FAILED/CANCELLED。

本模块**不依赖** Redis 与数据库，只依赖 researchclaw + 同目录的 task_config_crypto。
"""

# ============================================================================
# 在导入任何第三方库之前，先禁用所有可能的颜色输出
# 这样可以防止 researchclaw / llm4ad 内部的 loguru 输出 ANSI 颜色码
# ============================================================================
import os
import sys

os.environ["NO_COLOR"] = "1"
os.environ["LOGURU_COLORIZE"] = "false"
os.environ["FORCE_COLOR"] = "0"
os.environ["TERM"] = "dumb"
os.environ["CLICOLOR"] = "0"
os.environ["ANSI_COLORS_DISABLED"] = "1"

# 如果 loguru 已经被导入（例如在 researchclaw 中），重新配置它
try:
    from loguru import logger as loguru_logger
    # 移除默认 handler 并添加一个无颜色的 handler
    loguru_logger.remove()
    loguru_logger.add(
        sys.stderr,
        format="{level: <8} {time:YYYY-MM-DD HH:mm:ss.SSS} | {name}:{function}:{line} - {message}",
        level="INFO",
        colorize=False,
    )
except ImportError:
    pass  # loguru 未安装，跳过
# ============================================================================

import json
import logging
import threading
import traceback
from pathlib import Path

# 与本文件同目录，容器内以脚本方式启动时 sys.path[0] 即本目录
import task_config_crypto  # noqa: E402

# run_dir 的容器内挂载路径，须与 app.core.constants.RESEARCH_CONTAINER_DATA_DIR 一致
DATA_DIR = "/research/run"
CONFIG_FILENAME = ".app_config.json"          # = app.core.constants.APP_CONFIG_FILENAME
# 事件文件名由宿主经 env 传入 per-turn 值（.events-<turn_id>.jsonl），避免多轮共享
# 同一文件时宿主 tailer 从 offset 0 重读旧轮事件。缺省回退旧的 session 级共享名。
EVENTS_FILENAME = os.environ.get("RESEARCH_EVENTS_FILENAME") or ".events.jsonl"

# 默认 HITL mode（无人干预档）。本模块零 app.* 依赖，不复用宿主侧同名常量，自持一份。
_DEFAULT_HITL_MODE = "full-auto"

logger = logging.getLogger("research_container_runner")


class EventsSink:
    """线程安全地向 NDJSON 事件文件追加事件。可作上下文管理器。"""

    def __init__(self, events_path: str) -> None:
        # "w" 覆盖：per-turn 文件正常是新文件，但同 turn retry/resume 会复用同名
        # 路径——覆盖清掉上一次残留，杜绝宿主从头 tail 时读到本 turn 上一次的事件。
        self._fp = open(events_path, "w", encoding="utf-8")  # noqa: SIM115 - 由 close 释放
        self._lock = threading.Lock()

    def emit(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self._fp.write(line)
                self._fp.flush()
            except Exception as exc:
                # 事件文件写失败若静默吞掉，宿主永远收不到 __result__ 终态，只能退回
                # 据 exit_code 兜底猜测（见 task._marker_from_container_status）。至少把
                # 失败打到 stderr——宿主 on_stdout 会捕获，便于定位是哪个事件丢了。
                etype = event.get("type", "?")
                print(f"[EventsSink] emit failed type={etype}: {exc}", file=sys.stderr, flush=True)  # noqa: T201 - 容器入口，logger 会经 handler 回灌 sink 造成递归

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass

    def __enter__(self) -> "EventsSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class _ArcEventsLogHandler(logging.Handler):
    """把 researchclaw 的 stdlib logging record 转成 ``type=log`` 事件写事件文件。

    事件形状（type/level/message/module/source=arc）与宿主 sink 期望一致，宿主
    ``on_event`` 收到后原样 ``sink.emit``。
    """

    def __init__(self, sink: EventsSink) -> None:
        super().__init__(level=logging.INFO)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        try:
            self._sink.emit({
                "type": "log",
                "level": record.levelname,
                "message": message,
                "module": record.name,
                "source": "arc",
            })
        except Exception:
            self.handleError(record)


def _resolve_stage(text):
    """把 stage 规格（数字串 / 名字 / None）解析成 ARC ``Stage`` 枚举或 None。"""
    from researchclaw.pipeline.stages import Stage

    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        return Stage(int(s))
    except ValueError:
        pass
    try:
        return Stage[s.upper()]
    except (KeyError, ValueError):
        return None


def _write_guidance(guidance, from_stage) -> None:
    """把用户文字反馈写成目标 stage 的 ``hitl_guidance.md``（空则跳过）。

    与 ARC 的 ``guide`` 命令 / gate ``INJECT`` 产出同款：``stage-{NN}/hitl_guidance.md``，
    ARC 跑到该 stage 时 glob 进 LLM prompt 前言。目标 stage 取 ``from_stage`` 的
    stage 号（gate 恢复已算好正确阶段），无则落 stage 1。
    """
    text = (guidance or "").strip()
    if not text:
        return
    # from_stage 是 researchclaw Stage 枚举；取 .value 兜底非 IntEnum 情形，
    # 避免 int() 抛 TypeError（不被下方 except OSError 捕获）。
    try:
        stage_num = int(getattr(from_stage, "value", from_stage)) if from_stage is not None else 1
    except (TypeError, ValueError):
        stage_num = 1
    try:
        stage_dir = os.path.join(DATA_DIR, f"stage-{stage_num:02d}")
        os.makedirs(stage_dir, exist_ok=True)
        with open(os.path.join(stage_dir, "hitl_guidance.md"), "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"wrote hitl_guidance.md to stage-{stage_num:02d} ({len(text)} chars)")
    except OSError:
        logger.exception("write hitl_guidance.md failed")


class _PipelinePauseSignal(BaseException):
    """HITL 命中暂停时抛出的信号，携带原生 ``WaitingState``。

    继承 ``BaseException`` 而非 ``Exception`` 是刻意为之：researchclaw 的
    ``HITLSession.wait_for_human`` 用 ``except Exception`` 包住 input callback，
    普通异常会被吞掉退回文件轮询；``BaseException`` 能穿透该 except 一路传到
    容器 ``main()``，实现「命中门控→干净返回→结束容器」而非进程内阻塞死等。
    """

    def __init__(self, waiting: object) -> None:
        self.waiting = waiting
        super().__init__("pipeline paused for HITL")


def _pause_callback(waiting):
    """HITLSession 的 input callback：不阻塞，直接抛信号交由 main 处理。"""
    raise _PipelinePauseSignal(waiting)


def _build_hitl_session(hitl_mode, run_id, run_dir):
    """按前端 mode 构造原生 ``HITLSession`` 并挂非阻塞 callback。

    直接复用 researchclaw 的 ``get_preset``：在哪停、每个 stage 什么 policy、
    命中时给哪些 ``available_actions`` 全部由原生决定，与 CLI ``--mode`` 逐字节
    对齐。preset 缺失时回落 ``HITLConfig(enabled=True, mode=mode)``（与 CLI
    ``cmd_run`` 同款 fallback）。``full-auto`` → ``autonomous_preset`` →
    ``enabled=False`` → ``should_pause_after`` 恒 False → 全程不停。
    """
    from researchclaw.hitl.config import HITLConfig
    from researchclaw.hitl.presets import get_preset
    from researchclaw.hitl.session import HITLSession

    mode = (hitl_mode or _DEFAULT_HITL_MODE).strip() or _DEFAULT_HITL_MODE
    cfg = get_preset(mode)
    if cfg is None:
        cfg = HITLConfig(enabled=True, mode=mode)
    session = HITLSession(run_id=run_id, config=cfg, run_dir=run_dir)
    session.set_input_callback(_pause_callback)
    return session


def _marker_from_pause(waiting, progress_state: dict, run_dir: Path) -> dict:
    """把原生 ``WaitingState`` 转成 gate 终态标记（含原生 available_actions）。

    ``gate_context_summary`` 采用 researchclaw 的结构化摘要
    :func:`researchclaw.hitl.summarizer.generate_pause_summary`（分阶段引导 +
    产物预览 + 质量分 + 动态统计，纯本地读文件、不调 LLM），替代原生
    ``WaitingState.context_summary`` 的「产物截断拼接」。摘要失败时回退到原生串。
    """
    stage_obj = getattr(waiting, "stage", 0)
    stage_num = int(stage_obj) if stage_obj is not None else 0
    stage_name = str(getattr(waiting, "stage_name", "") or "")
    reason = getattr(waiting, "reason", None)
    reason_str = reason.value if hasattr(reason, "value") else str(reason or "")

    try:
        from researchclaw.hitl.summarizer import generate_pause_summary

        summary = generate_pause_summary(stage_num, stage_name, run_dir)
    except Exception:
        summary = str(getattr(waiting, "context_summary", "") or "")

    return {
        "outcome": "gate",
        "gate_stage": stage_num,
        "gate_stage_name": stage_name,
        "gate_reason": reason_str,
        "gate_context_summary": summary,
        "gate_output_files": list(getattr(waiting, "output_files", ()) or ()),
        "gate_available_actions": list(getattr(waiting, "available_actions", ()) or []),
        "stages_done": int(progress_state.get("done", 0)),
        "stages_failed": 0,
    }


def _marker_from_stage_results(stage_results: list) -> dict:
    """把 execute_pipeline 的 StageResult 列表汇总成终态标记 dict。

    门控停返不走这里：容器以 ``auto_approve_gates=True`` 关掉了 ARC 粗门控，硬门控
    停点由原生 ``HITLSession`` 抛 ``_PipelinePauseSignal`` 处理（见 :func:`main`），
    故 stage_results 里不会出现 ``blocked_approval`` 状态。
    """
    done = sum(1 for r in stage_results
               if getattr(r, "status", None) and str(r.status.value) == "done")
    failed = sum(1 for r in stage_results
                 if getattr(r, "status", None) and str(r.status.value) == "failed")

    if failed > 0:
        return {"outcome": "failed", "stages_done": done, "stages_failed": failed,
                "error": f"{failed} stage(s) failed, {done} done"}
    return {"outcome": "done", "stages_done": done, "stages_failed": failed}


def main() -> None:
    """容器入口：解密配置 → 跑 execute_pipeline → 写事件流与终态标记。"""
    try:
        # 最早处取出并删除解密密钥，避免被子进程继承或读取
        config_key = os.environ.pop("RESEARCH_CONFIG_KEY", None)
        config_path = os.path.join(DATA_DIR, CONFIG_FILENAME)

        with open(config_path, encoding="utf-8") as f:
            token = f.read()
        try:
            os.remove(config_path)  # 密文已入内存，磁盘不再保留
        except OSError:
            pass
        if not config_key:
            raise RuntimeError("缺少 RESEARCH_CONFIG_KEY，无法解密研究配置")
        data = task_config_crypto.decrypt_config(token, config_key)
        del token, config_key

        arc_config_dict = data["arc_config"]
        from_stage = _resolve_stage(data.get("from_stage"))
        to_stage = _resolve_stage(data.get("to_stage"))
        hitl_mode = data.get("hitl_mode")
        skip_noncritical = bool(data.get("skip_noncritical", True))
        run_id = str(data.get("run_id") or "run")
        guidance = data.get("guidance")

        os.chdir(DATA_DIR)

        # 用户文字反馈 → 写成目标 stage 的 hitl_guidance.md，被 ARC 的
        # _build_context_preamble glob 进 LLM prompt 前言（等价 CLI guide / gate
        # INJECT）。目标 stage = from_stage（gate 恢复已算好），缺省落 stage 1。
        _write_guidance(guidance, from_stage)

        from researchclaw.adapters import AdapterBundle
        from researchclaw.config import RCConfig
        from researchclaw.pipeline.runner import execute_pipeline

        arc_config = RCConfig.from_dict(
            arc_config_dict, project_root=DATA_DIR, check_paths=False
        )
        adapters = AdapterBundle()
        # 挂原生 HITLSession：门控「在哪停 / 每 stage 什么 policy / 命中给哪些
        # available_actions」全部由原生 preset 决定，与 CLI --mode 一一对应。
        # callback 非阻塞（抛 _PipelinePauseSignal），命中即干净结束容器。
        adapters.hitl = _build_hitl_session(hitl_mode, run_id, Path(DATA_DIR))

        kb_root = None
        try:
            if arc_config.knowledge_base.root:
                kb_root = arc_config.knowledge_base.root
                os.makedirs(kb_root, exist_ok=True)
        except Exception:
            kb_root = None

        with EventsSink(os.path.join(DATA_DIR, EVENTS_FILENAME)) as events:
            # 挂 ARC 根 logger → 事件文件（全量捕获 pipeline 内部日志）
            arc_logger = logging.getLogger("researchclaw")
            arc_logger.setLevel(logging.INFO)
            handler = _ArcEventsLogHandler(events)
            arc_logger.addHandler(handler)

            # stage 进度：把 ARC 原始 payload 打包成 __progress__ 事件，宿主翻译。
            # 顺带累计已完成 stage 数——命中暂停信号时没有 stage_results 列表，
            # gate marker 的 stages_done 从这里取。
            progress_state = {"done": 0}

            def progress_callback(payload: dict) -> None:
                try:
                    if (
                        payload.get("type") == "stage_end"
                        and str(payload.get("status") or "") == "done"
                    ):
                        progress_state["done"] += 1
                except Exception:
                    pass
                events.emit({"type": "__progress__", "payload": payload})

            kwargs = {
                # researchclaw 内部对 run_dir 做 ``run_dir / f"stage-{n:02d}"``，
                # 要求是 Path 而非 str，故这里显式包 Path（DATA_DIR 是容器内绝对路径）。
                "run_dir": Path(DATA_DIR),
                "run_id": run_id,
                "config": arc_config,
                "adapters": adapters,
                # 门控统一由 HITLSession（post-stage hook）承担；粗门控
                # gate_required 用 auto_approve_gates=True 关掉，避免 5/9/20 双触发。
                "auto_approve_gates": True,
                "stop_on_gate": False,
                "skip_noncritical": skip_noncritical,
                "kb_root": kb_root,
                "progress_callback": progress_callback,
            }
            if from_stage is not None:
                kwargs["from_stage"] = from_stage
            if to_stage is not None:
                kwargs["to_stage"] = to_stage

            try:
                stage_results = execute_pipeline(**kwargs)
                marker = _marker_from_stage_results(stage_results)
            except _PipelinePauseSignal as pause:
                # 命中 HITL 门控：从原生 WaitingState 提取 stage/actions/reason，
                # 合成 gate marker，容器干净结束、worker 释放。用户回复经宿主
                # _reply_to_gate 新建一轮 turn 从断点续跑。
                marker = _marker_from_pause(
                    pause.waiting, progress_state, Path(DATA_DIR)
                )
            finally:
                try:
                    arc_logger.removeHandler(handler)
                except Exception:
                    pass

            # 终态经 events 通道回传（不再写 per-session 的 .research_result.json —
            # 那个文件多轮复用同一路径，容器被 kill/崩时宿主会读到上一轮残留）。
            # __result__ 事件在 EventsSink 块内发，随后由 ContainerJob 的 final drain
            # 保证送达；容器若在此之前崩溃/被 kill，宿主据 result.status 兜底判定。
            events.emit({"type": "__result__", "marker": marker})

        outcome = marker.get("outcome")
        logger.info(f"research pipeline finished outcome={outcome}")

    except Exception as exc:
        logger.error(f"research pipeline failed: {exc}")
        traceback.print_exc()
        # 不写终态文件：非零退出即让宿主据 result.status 判 FAILED（演化同款）。
        # 具体 error 已经 print 到 stdout，由宿主 on_stdout 转发进事件流。
        sys.exit(1)


if __name__ == "__main__":
    main()
