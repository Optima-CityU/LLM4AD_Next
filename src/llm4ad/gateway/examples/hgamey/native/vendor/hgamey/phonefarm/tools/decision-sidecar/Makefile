# 决策侧车: 把 Core ML 决策模型接进 `phonefarm decide --sidecar`。
#
#   make build    # 编译侧车(Swift, 系统自带 CoreML 框架)
#   make test     # 内置自检: 加载模型 + 图自检 + 三种题型各跑一次并打点
#   make probe    # 只验模型图(s_i = dot(state, options_i))
#   make model    # 重新生成参考模型(需要 coremltools; reference.mlmodel 已入库, 平时不用跑)
#   make clean
#
# 只需要 macOS + Xcode 命令行工具。不下载任何东西。
#
# 参考模型 137 字节, 已入库 —— 这样只有 Xcode 的人也能 `make build && make test` 把整条
# 通路跑起来, 不必先装 coremltools。真正要换模型时才需要它(见 README.md)。
#
# 注意: 输出目录叫 build/, 而 build 同时是个 .PHONY 目标 —— 所以下面一律用
# `mkdir -p $(@D)`, 不把目录写成目标(否则 make 会说 "Circular … dependency dropped")。

SWIFTC  ?= swiftc
PYTHON  ?= python3
OUT     := build
MODEL   := reference.mlmodel
BIN     := $(OUT)/decision-sidecar

.PHONY: all build test probe model clean
all: build

# -framework CoreML/Foundation 都是系统的; -O 让编码器不至于成为瓶颈
$(BIN): sidecar.swift
	@mkdir -p $(@D)
	$(SWIFTC) -O -o $@ $< -framework CoreML -framework Foundation
	@echo "built $@"

$(MODEL): reference-model.py
	$(PYTHON) reference-model.py $@

build: $(BIN)
probe: $(BIN) $(MODEL)
	$(BIN) --probe --model $(MODEL)
test: $(BIN) $(MODEL)
	$(BIN) --probe --model $(MODEL)
	$(BIN) --selftest --model $(MODEL)
model: $(MODEL)

clean:
	rm -rf $(OUT)
