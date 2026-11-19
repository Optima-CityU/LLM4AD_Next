#!/bin/sh
# 拉取 TFLite benchmark_model 官方 nightly 预编译二进制 (Android arm64, 内置 GPU Delegate)。
# 二进制不入库 (AGENTS.md: 严禁提交大体积非文本文件); phonefarm bench 缺省从本目录取。
set -e
cd "$(dirname "$0")"
BASE=https://storage.googleapis.com/tensorflow-nightly-public/prod/tensorflow/release/lite/tools/nightly/latest
curl -fSL -o android_aarch64_benchmark_model "$BASE/android_aarch64_benchmark_model"
chmod +x android_aarch64_benchmark_model
ls -l android_aarch64_benchmark_model
